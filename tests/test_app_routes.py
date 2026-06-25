import importlib

import pytest


@pytest.fixture
def client(servers_file, monkeypatch):
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.app.config["TESTING"] = True
    return app_module, app_module.app.test_client()


def test_no_active_server_shows_clear_message(client):
    app_module, test_client = client
    import servers
    importlib.reload(servers)
    only = servers.list_servers()[0]
    servers.update_server(only["id"], enabled=False)
    resp = test_client.get("/models")
    assert resp.status_code == 200
    assert b"No active server configured" in resp.data


def test_models_page_renders_with_mocked_tags(client, monkeypatch):
    app_module, test_client = client

    class FakeClient:
        def __init__(self, base_url=None): pass
        def tags(self):
            return {"models": [{"name": "llama3.2", "size": 100, "details": {}}]}

    monkeypatch.setattr(app_module, "OllamaClient", FakeClient)
    resp = test_client.get("/models")
    assert resp.status_code == 200
    assert b"llama3.2" in resp.data


def test_add_and_list_servers(client, monkeypatch):
    app_module, test_client = client
    resp = test_client.post("/servers/add", data={
        "name": "Remote", "base_url": "http://10.0.0.5:11434"
    }, follow_redirects=True)
    assert resp.status_code == 200
    import servers
    assert any(s["name"] == "Remote" for s in servers.list_servers())


def test_set_active_server(client):
    app_module, test_client = client
    import servers
    s = servers.add_server("Remote", "http://10.0.0.6:11434")
    resp = test_client.post("/servers/active", data={"server_id": s["id"]},
                            follow_redirects=True)
    assert resp.status_code == 200
    with test_client.session_transaction() as sess:
        assert sess["active_server_id"] == s["id"]


def test_delete_last_server_flashes_error(client):
    app_module, test_client = client
    import servers
    only = servers.list_servers()[0]
    resp = test_client.post(f"/servers/{only['id']}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert len(servers.list_servers()) == 1
    assert b"cannot delete the last server" in resp.data


def test_add_duplicate_server_flashes_error(client):
    app_module, test_client = client
    import servers
    servers.add_server("Remote", "http://10.0.0.7:11434")
    resp = test_client.post("/servers/add", data={
        "name": "Dup", "base_url": "http://10.0.0.7:11434"
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"already exists" in resp.data
    # still only the seeded server + the one Remote we added
    assert len([s for s in servers.list_servers() if s["base_url"] == "http://10.0.0.7:11434"]) == 1


def test_pull_enqueue_and_jobs(client, monkeypatch):
    app_module, test_client = client
    import pull_jobs, servers
    pull_jobs.reset()

    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    s = servers.list_servers()[0]
    r = test_client.post("/pull/enqueue", json={"model": "llama3.2", "server_ids": [s["id"]]})
    assert r.status_code == 200
    assert r.get_json()["jobs"][0]["model"] == "llama3.2"
    r2 = test_client.get("/pull/jobs")
    assert any(j["model"] == "llama3.2" for j in r2.get_json()["jobs"])


def test_pull_enqueue_requires_model(client):
    app_module, test_client = client
    r = test_client.post("/pull/enqueue", json={"server_ids": ["x"]})
    assert r.status_code == 400


def test_pull_enqueue_rejects_unknown_servers(client):
    app_module, test_client = client
    r = test_client.post("/pull/enqueue", json={"model": "m", "server_ids": ["nope"]})
    assert r.status_code == 400


def test_pull_cancel_route(client, monkeypatch):
    app_module, test_client = client
    import pull_jobs, servers
    pull_jobs.reset()
    gate = __import__("threading").Event()

    class FakeResp:
        status_code = 200
        def iter_lines(self):
            gate.wait(2)
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    s = servers.list_servers()[0]
    job = pull_jobs.enqueue(s, "m")
    r = test_client.post("/pull/cancel/" + job["id"])
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    gate.set()


def test_build_create_payload_includes_num_ctx_and_params(client):
    app_module, _ = client
    payload = app_module.build_create_payload({
        "model_name": "m", "from_model": "base", "num_ctx": "8192",
        "parameters": [{"key": "temperature", "value": "0.5"},
                       {"key": "stop", "value": "END"}],
    }, stream=True)
    assert payload["from"] == "base"
    assert payload["parameters"]["num_ctx"] == 8192
    assert payload["parameters"]["temperature"] == 0.5
    assert payload["parameters"]["stop"] == "END"
    assert payload["stream"] is True


def test_create_stream_forwards(client, monkeypatch):
    app_module, test_client = client
    import servers
    s = servers.list_servers()[0]

    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"creating"}'

    class FakeClient:
        def __init__(self, *a, **k): pass
        def create(self, payload, stream=False): return FakeResp()

    monkeypatch.setattr(app_module, "OllamaClient", FakeClient)
    resp = test_client.post("/create-model/stream", json={
        "server_id": s["id"], "model_name": "m", "from_model": "base", "num_ctx": "4096"
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '"creating"' in body
    assert '"done": true' in body


def test_create_stream_requires_server_and_model(client):
    app_module, test_client = client
    # missing model_name
    r1 = test_client.post("/create-model/stream", json={"server_id": "whatever"})
    assert r1.status_code == 400
    # unknown server_id
    r2 = test_client.post("/create-model/stream", json={"server_id": "nope", "model_name": "m"})
    assert r2.status_code == 400


def test_broadcast_delete_collects_per_server_results(client, monkeypatch):
    app_module, _ = client
    import servers
    s1 = servers.list_servers()[0]
    s2 = servers.add_server("Remote", "http://10.0.0.9:11434")

    class OKResp: status_code = 200
    class FailClient:
        def __init__(self, base_url): self.base_url = base_url
        def delete(self, model):
            if "10.0.0.9" in self.base_url:
                raise RuntimeError("unreachable")
            return OKResp()

    monkeypatch.setattr(app_module, "OllamaClient", FailClient)
    results = app_module.broadcast_delete("llama3.2", [s1["id"], s2["id"]])
    ok = {r["name"]: r["ok"] for r in results}
    assert ok[s1["name"]] is True
    assert ok["Remote"] is False


def test_create_stream_rejects_bad_num_ctx(client):
    app_module, test_client = client
    import servers
    s = servers.list_servers()[0]
    resp = test_client.post("/create-model/stream", json={
        "server_id": s["id"], "model_name": "m", "from_model": "base", "num_ctx": "not-a-number"
    })
    assert resp.status_code == 400


def test_create_stream_rejects_empty_from(client):
    app_module, test_client = client
    import servers
    s = servers.list_servers()[0]
    resp = test_client.post("/create-model/stream", json={
        "server_id": s["id"], "model_name": "m",
        "creation_method": "from_model", "from_model": "",
    })
    assert resp.status_code == 400
    assert b"base model" in resp.data


def test_merged_models_marks_drift_and_survives_offline(client, monkeypatch):
    app_module, _ = client
    import servers
    s1 = servers.list_servers()[0]
    s2 = servers.add_server("Remote", "http://10.0.0.10:11434")
    s3 = servers.add_server("Down", "http://10.0.0.11:11434")

    class C:
        def __init__(self, base_url): self.base_url = base_url
        def tags(self):
            if "10.0.0.11" in self.base_url:
                raise RuntimeError("offline")
            if "10.0.0.10" in self.base_url:
                return {"models": [{"name": "shared", "size": 1, "details": {}}]}
            return {"models": [
                {"name": "shared", "size": 1, "details": {}},
                {"name": "only1", "size": 2, "details": {}}]}

    monkeypatch.setattr(app_module, "OllamaClient", C)
    models_list, status = app_module.merged_models(servers.get_enabled())
    by_name = {m["name"]: m for m in models_list}
    assert status[s3["id"]] is False
    assert by_name["only1"]["is_drift"] is True
    assert s2["id"] in [srv["id"] for srv in by_name["only1"]["missing_on"]]
    assert by_name["shared"]["is_drift"] is False
