import json
import os
import threading
import uuid

_LOCK = threading.RLock()


def _servers_file():
    return os.environ.get(
        "WEBOLLAMA_SERVERS_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers.json"),
    )


def normalize_url(url):
    u = (url or "").strip().rstrip("/")
    if u.endswith("/api"):
        u = u[: -len("/api")]
    return u.rstrip("/")


def _seed():
    base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    return {
        "servers": [
            {
                "id": uuid.uuid4().hex,
                "name": "Default",
                "base_url": normalize_url(base),
                "enabled": True,
            }
        ]
    }


def _read():
    path = _servers_file()
    if not os.path.exists(path):
        with _LOCK:
            if not os.path.exists(path):
                _write(_seed())
    with open(path, "r") as f:
        return json.load(f)


def _write(data):
    path = _servers_file()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def list_servers():
    return _read()["servers"]


def get_enabled():
    return [s for s in list_servers() if s.get("enabled", True)]


def get_server(server_id):
    for s in list_servers():
        if s["id"] == server_id:
            return s
    return None


def add_server(name, base_url, enabled=True):
    name = (name or "").strip()
    norm = normalize_url(base_url)
    if not name or not norm:
        raise ValueError("name and base_url are required")
    with _LOCK:
        data = _read()
        for s in data["servers"]:
            if normalize_url(s["base_url"]) == norm:
                raise ValueError("a server with this base_url already exists")
        server = {"id": uuid.uuid4().hex, "name": name, "base_url": norm, "enabled": bool(enabled)}
        data["servers"].append(server)
        _write(data)
        return server


def update_server(server_id, name=None, base_url=None, enabled=None):
    with _LOCK:
        data = _read()
        target = next((s for s in data["servers"] if s["id"] == server_id), None)
        if target is None:
            raise ValueError("server not found")
        if name is not None:
            target["name"] = name.strip()
        if base_url is not None:
            target["base_url"] = normalize_url(base_url)
        if enabled is not None:
            target["enabled"] = bool(enabled)
        _write(data)
        return target


def delete_server(server_id):
    with _LOCK:
        data = _read()
        if len(data["servers"]) <= 1:
            raise ValueError("cannot delete the last server")
        data["servers"] = [s for s in data["servers"] if s["id"] != server_id]
        _write(data)


def get_active_id(session):
    enabled = get_enabled()
    if not enabled:
        return None
    enabled_ids = {s["id"] for s in enabled}
    active = session.get("active_server_id")
    if active in enabled_ids:
        return active
    return enabled[0]["id"]


def set_active(session, server_id):
    session["active_server_id"] = server_id
