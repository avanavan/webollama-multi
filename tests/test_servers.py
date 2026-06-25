import importlib
import json

import pytest


def _fresh_servers():
    import servers
    return importlib.reload(servers)


def test_first_read_seeds_default_from_env(servers_file):
    servers = _fresh_servers()
    items = servers.list_servers()
    assert len(items) == 1
    assert items[0]["name"] == "Default"
    assert items[0]["base_url"] == "http://127.0.0.1:11434"
    assert items[0]["enabled"] is True
    assert servers_file.exists()


def test_normalize_url_strips_api_and_trailing_slash(servers_file):
    servers = _fresh_servers()
    assert servers.normalize_url("http://h:11434/") == "http://h:11434"
    assert servers.normalize_url("http://h:11434/api") == "http://h:11434"
    assert servers.normalize_url("  http://h:11434/api/  ") == "http://h:11434"


def test_add_server_appends_and_persists(servers_file):
    servers = _fresh_servers()
    created = servers.add_server("Remote", "http://10.0.0.5:11434")
    assert created["id"]
    assert created["name"] == "Remote"
    on_disk = json.loads(servers_file.read_text())
    assert len(on_disk["servers"]) == 2


def test_add_server_rejects_duplicate_url(servers_file):
    servers = _fresh_servers()
    servers.add_server("Remote", "http://10.0.0.5:11434/api")
    with pytest.raises(ValueError):
        servers.add_server("Dup", "http://10.0.0.5:11434")


def test_add_server_requires_name_and_url(servers_file):
    servers = _fresh_servers()
    with pytest.raises(ValueError):
        servers.add_server("", "http://h:11434")
    with pytest.raises(ValueError):
        servers.add_server("x", "")


def test_update_server_changes_fields(servers_file):
    servers = _fresh_servers()
    s = servers.add_server("Remote", "http://10.0.0.5:11434")
    servers.update_server(s["id"], name="Renamed", enabled=False)
    got = servers.get_server(s["id"])
    assert got["name"] == "Renamed"
    assert got["enabled"] is False


def test_get_enabled_filters_disabled(servers_file):
    servers = _fresh_servers()
    s = servers.add_server("Remote", "http://10.0.0.5:11434")
    servers.update_server(s["id"], enabled=False)
    enabled = servers.get_enabled()
    assert all(x["enabled"] for x in enabled)
    assert len(enabled) == 1


def test_delete_server_removes(servers_file):
    servers = _fresh_servers()
    s = servers.add_server("Remote", "http://10.0.0.5:11434")
    servers.delete_server(s["id"])
    assert servers.get_server(s["id"]) is None


def test_delete_last_server_raises(servers_file):
    servers = _fresh_servers()
    only = servers.list_servers()[0]
    with pytest.raises(ValueError):
        servers.delete_server(only["id"])


def test_active_id_defaults_to_first_enabled(servers_file):
    servers = _fresh_servers()
    session = {}
    assert servers.get_active_id(session) == servers.list_servers()[0]["id"]


def test_active_id_falls_back_when_stale(servers_file):
    servers = _fresh_servers()
    session = {"active_server_id": "does-not-exist"}
    assert servers.get_active_id(session) == servers.get_enabled()[0]["id"]


def test_set_active_records_in_session(servers_file):
    servers = _fresh_servers()
    s = servers.add_server("Remote", "http://10.0.0.5:11434")
    session = {}
    servers.set_active(session, s["id"])
    assert session["active_server_id"] == s["id"]
    assert servers.get_active_id(session) == s["id"]
