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
        def tags(self):
            return {"models": [{"name": "llama3.2", "size": 100, "details": {}}]}

    monkeypatch.setattr(app_module, "active_client", lambda: FakeClient())
    monkeypatch.setattr(app_module, "client_for", lambda sid: FakeClient())
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
    with test_client.session_transaction() as sess:
        pass
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
