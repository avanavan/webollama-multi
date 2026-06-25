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
