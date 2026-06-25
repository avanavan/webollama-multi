# WebOllama Multi-Server Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one WebOllama instance manage multiple Ollama servers — sync pull/delete/create mutations across them, show per-server progress bars, reconcile model drift, and create models with a custom `num_ctx` plus free-form parameters.

**Architecture:** Light modularization of the existing single-file Flask app. Two new modules — `servers.py` (JSON-persisted server registry + active-server selection) and `ollama_client.py` (per-server HTTP client) — replace the two hardcoded `OLLAMA_API_BASE`/`OLLAMA_API_URL` globals. Routes in `app.py` resolve a client per server. Multi-server progress streaming uses one SSE connection per server (frontend opens N parallel readers); no backend multiplexing.

**Tech Stack:** Python 3.7+, Flask 3.1, requests 2.32, flask-wtf (CSRF), Bootstrap 5.3, Font Awesome, Jinja2. Tests: pytest + responses (HTTP mocking).

## Global Constraints

- Python 3.7+ compatible (no `match`, no `|` union types in annotations).
- No new runtime dependencies beyond what's in `requirements.txt` (flask, requests, python-dotenv, flask-wtf, markdown). Test-only deps go in `requirements-dev.txt`.
- All Ollama HTTP access goes through `ollama_client.OllamaClient`. No new direct `requests` calls to Ollama endpoints in `app.py`.
- `base_url` is stored normalized (scheme+host+port, no trailing `/` and no `/api` suffix). The client appends `/api`.
- Server list persists to a JSON file at `WEBOLLAMA_SERVERS_FILE` (default `servers.json` next to `app.py`). On first run, seed one server named `Default` from `OLLAMA_API_BASE` (default `http://127.0.0.1:11434`).
- All new POST routes are CSRF-protected (flask-wtf is global). Streaming `fetch` calls send the `X-CSRFToken` header (existing pattern in `create_model.html`).
- SSE event contract: each forwarded line is `data: <raw ollama json>\n\n`; success terminates with `data: {"done": true}\n\n`; failure emits `data: {"error": "<msg>"}\n\n`.
- `app.run(...)` must include `threaded=True` so concurrent per-server streams don't serialize.
- `servers.json` is gitignored.

---

## File Structure

**New files:**
- `servers.py` — server registry: load/save `servers.json`, CRUD, normalization, seeding, active-id resolution.
- `ollama_client.py` — `OllamaClient(base_url)` wrapping all Ollama API calls + `ping()`.
- `templates/servers.html` — server management page.
- `requirements-dev.txt` — `pytest`, `responses`.
- `tests/conftest.py` — pytest fixtures (temp servers file, Flask test client).
- `tests/test_servers.py`, `tests/test_ollama_client.py`, `tests/test_app_routes.py`.
- `pytest.ini` — test config.

**Modified files:**
- `app.py` — remove server globals; route through `servers`/`OllamaClient`; add server-management, active-switch, `/pull/stream`, `/create-model/stream` routes; broadcast delete; build create `parameters`; merged models view; `threaded=True`.
- `templates/base.html` — navbar server switcher + `/servers` sidebar link.
- `templates/pull_model.html` — target checkboxes + per-server progress bars.
- `templates/create_model.html` — `num_ctx` + free-form params + target checkboxes + per-server progress bars.
- `templates/models.html` — merged presence badges, drift banner, sync actions.
- `static/css/style.css` — progress-card / presence-badge styling.
- `README.md`, `.env.example`, `.gitignore`.

---

## Task 1: Test tooling + `servers.py` registry

**Files:**
- Create: `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py`, `tests/test_servers.py`, `servers.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces (`servers.py`):
  - `list_servers() -> list[dict]`
  - `get_enabled() -> list[dict]`
  - `get_server(server_id: str) -> dict | None`
  - `add_server(name: str, base_url: str, enabled: bool = True) -> dict` (raises `ValueError` on bad/dup input)
  - `update_server(server_id, name=None, base_url=None, enabled=None) -> dict`
  - `delete_server(server_id) -> None` (raises `ValueError` if it's the last server)
  - `get_active_id(session) -> str | None`
  - `set_active(session, server_id) -> None`
  - `normalize_url(url: str) -> str`
  - Server record dict: `{"id": str, "name": str, "base_url": str, "enabled": bool}`

- [ ] **Step 1: Create dev dependencies and pytest config**

Create `requirements-dev.txt`:
```
-r requirements.txt
pytest==8.3.4
responses==0.25.6
```

Create `pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

Add to `.gitignore` (append):
```
servers.json
.pytest_cache/
```

- [ ] **Step 2: Create the test fixture for an isolated servers file**

Create `tests/conftest.py`:
```python
import os
import pytest


@pytest.fixture
def servers_file(tmp_path, monkeypatch):
    """Point servers.py at a throwaway JSON file and a known seed URL."""
    path = tmp_path / "servers.json"
    monkeypatch.setenv("WEBOLLAMA_SERVERS_FILE", str(path))
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    return path
```

- [ ] **Step 3: Write failing tests for `servers.py`**

Create `tests/test_servers.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_servers.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'servers'`)

- [ ] **Step 5: Implement `servers.py`**

Create `servers.py`:
```python
import json
import os
import threading
import uuid

_LOCK = threading.Lock()


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
        data = _seed()
        _write(data)
        return data
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_servers.py -v`
Expected: PASS (12 passed)

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt pytest.ini tests/conftest.py tests/test_servers.py servers.py .gitignore
git commit -m "feat: add server registry with JSON persistence and seeding"
```

---

## Task 2: `ollama_client.py` per-server client

**Files:**
- Create: `ollama_client.py`, `tests/test_ollama_client.py`

**Interfaces:**
- Consumes: nothing (pure HTTP wrapper).
- Produces (`ollama_client.OllamaClient`):
  - `OllamaClient(base_url: str, timeout: int = 10)` with attribute `api == base_url.rstrip('/') + '/api'`
  - `version() -> dict`, `tags() -> dict`, `show(model) -> dict`, `ps() -> dict` (raise on HTTP error)
  - `pull(model, stream=False) -> requests.Response`
  - `delete(model) -> requests.Response`
  - `create(payload: dict, stream=False) -> requests.Response`
  - `generate(payload, stream=False) -> requests.Response`
  - `chat(payload, stream=True) -> requests.Response`
  - `ping() -> bool`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ollama_client.py`:
```python
import json

import responses

from ollama_client import OllamaClient


def test_api_url_appends_api():
    c = OllamaClient("http://h:11434")
    assert c.api == "http://h:11434/api"


@responses.activate
def test_tags_returns_json():
    responses.add(responses.GET, "http://h:11434/api/tags",
                  json={"models": [{"name": "a"}]}, status=200)
    c = OllamaClient("http://h:11434")
    assert c.tags() == {"models": [{"name": "a"}]}


@responses.activate
def test_show_posts_model():
    responses.add(responses.POST, "http://h:11434/api/show",
                  json={"details": {}}, status=200)
    c = OllamaClient("http://h:11434")
    c.show("llama3.2")
    assert json.loads(responses.calls[0].request.body) == {"model": "llama3.2"}


@responses.activate
def test_delete_sends_model():
    responses.add(responses.DELETE, "http://h:11434/api/delete", status=200)
    c = OllamaClient("http://h:11434")
    r = c.delete("llama3.2")
    assert r.status_code == 200
    assert json.loads(responses.calls[0].request.body) == {"model": "llama3.2"}


@responses.activate
def test_create_posts_payload():
    responses.add(responses.POST, "http://h:11434/api/create", status=200)
    c = OllamaClient("http://h:11434")
    c.create({"model": "m", "from": "base", "parameters": {"num_ctx": 8192}})
    body = json.loads(responses.calls[0].request.body)
    assert body["parameters"]["num_ctx"] == 8192


@responses.activate
def test_ping_true_on_200():
    responses.add(responses.GET, "http://h:11434/api/version",
                  json={"version": "0.1"}, status=200)
    assert OllamaClient("http://h:11434").ping() is True


@responses.activate
def test_ping_false_on_error():
    responses.add(responses.GET, "http://h:11434/api/version",
                  body=ConnectionError("down"))
    assert OllamaClient("http://h:11434").ping() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ollama_client.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'ollama_client'`)

- [ ] **Step 3: Implement `ollama_client.py`**

Create `ollama_client.py`:
```python
import requests


class OllamaClient:
    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api"
        self.timeout = timeout

    def version(self):
        r = requests.get(f"{self.api}/version", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def tags(self):
        r = requests.get(f"{self.api}/tags", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def show(self, model):
        r = requests.post(f"{self.api}/show", json={"model": model}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def ps(self):
        r = requests.get(f"{self.api}/ps", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def pull(self, model, stream=False):
        return requests.post(
            f"{self.api}/pull", json={"model": model, "stream": stream}, stream=stream
        )

    def delete(self, model):
        return requests.delete(f"{self.api}/delete", json={"model": model}, timeout=self.timeout)

    def create(self, payload, stream=False):
        return requests.post(f"{self.api}/create", json=payload, stream=stream)

    def generate(self, payload, stream=False):
        return requests.post(f"{self.api}/generate", json=payload, stream=stream)

    def chat(self, payload, stream=True):
        return requests.post(f"{self.api}/chat", json=payload, stream=stream)

    def ping(self):
        try:
            r = requests.get(f"{self.api}/version", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ollama_client.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add ollama_client.py tests/test_ollama_client.py
git commit -m "feat: add per-server OllamaClient wrapper"
```

---

## Task 3: Refactor `app.py` onto the registry + client (single-server parity)

This task swaps the two hardcoded globals for the registry/client while keeping behavior identical for the seeded single server. Single-server pages resolve the **active** server from the session.

**Files:**
- Modify: `app.py` (imports/config near lines 1-22; every route using `OLLAMA_API_URL`; the `if __name__` block at the bottom)

**Interfaces:**
- Consumes: `servers` module (Task 1), `ollama_client.OllamaClient` (Task 2).
- Produces (helpers added to `app.py`, used by later tasks):
  - `active_client() -> OllamaClient | None` — client for the session's active server.
  - `client_for(server_id) -> OllamaClient | None`
  - `coerce_param(value) -> int | float | bool | str`
  - `build_create_payload(data: dict) -> dict`

- [ ] **Step 1: Replace config block and add helpers**

In `app.py`, replace the Ollama config (the lines defining `OLLAMA_API_BASE` and `OLLAMA_API_URL`, ~16-17) with:
```python
import servers
from ollama_client import OllamaClient

# OLLAMA_API_BASE is now only the seed for the first server (see servers.py).


def client_for(server_id):
    rec = servers.get_server(server_id)
    return OllamaClient(rec["base_url"]) if rec else None


def active_client():
    return client_for(servers.get_active_id(session))


def coerce_param(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def build_create_payload(data, stream):
    payload = {"model": data.get("model_name"), "stream": stream}
    if data.get("system_prompt"):
        payload["system"] = data["system_prompt"]
    if data.get("template"):
        payload["template"] = data["template"]
    if data.get("from_model"):
        payload["from"] = data["from_model"]
    if data.get("quantize"):
        payload["quantize"] = data["quantize"]
    params = {}
    if data.get("num_ctx"):
        params["num_ctx"] = int(data["num_ctx"])
    for row in data.get("parameters", []):
        key = (row.get("key") or "").strip()
        if key:
            params[key] = coerce_param(row.get("value"))
    if params:
        payload["parameters"] = params
    return payload
```
Ensure `from flask import session` is present in the Flask import line (it already imports `request`, `render_template`, etc.; add `session` if missing).

- [ ] **Step 2: Route every existing single-server call through `active_client()`**

Replace each `requests.<verb>(f"{OLLAMA_API_URL}/<x>", ...)` in these handlers with the client call. Concretely:

- `/` (index) and `/version`, `/api/check-updates`: `client = active_client(); data = client.version()` (wrap in try/except; on failure keep the existing flash/error path).
- `/models`: replaced entirely in Task 8 — leave as-is for now but change its single call `requests.get(f"{OLLAMA_API_URL}/tags")` to `active_client().tags()` returning the same JSON shape so the page still renders.
- `/models/<model_name>` (detail): `active_client().show(model_name)`.
- `/models/delete/<model_name>`: replaced in Task 7 — for now `active_client().delete(model_name)`.
- `/models/update/<model_name>` and `/pull` POST: `active_client().pull(model_name, stream=False)` (returns a `requests.Response`; keep the existing `.status_code` checks).
- `/create` (GET form): `active_client().tags()` for the base-model list.
- `/create-model` POST non-streaming and `stream_create_model`: build payload via `build_create_payload(...)` and call `active_client().create(payload, stream=...)`.
- `/running-models`: `active_client().ps()` (Task 8 will optionally merge; keep single for now).
- `/models/unload/<model_name>`: `active_client().generate({"model": model_name, "keep_alive": 0}, stream=False)`.
- `/chat` GET and `/api/chat`: `active_client().tags()` / `active_client().chat(payload, stream=True)`.
- `/generate` GET and `/api/generate`: `active_client().tags()` / `active_client().generate(payload, stream=False)`.

Delete the now-unused `OLLAMA_API_URL` references. Keep `OLLAMA_API_BASE` import-time read out of app.py (servers.py owns it).

- [ ] **Step 3: Enable threaded server**

At the bottom of `app.py`, change:
```python
if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=True)
```
to:
```python
if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=True, threaded=True)
```

- [ ] **Step 4: Write a route smoke test (mocked client)**

Create `tests/test_app_routes.py` with this first test:
```python
import importlib

import pytest


@pytest.fixture
def client(servers_file, monkeypatch):
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.app.config["TESTING"] = True
    return app_module, app_module.app.test_client()


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
```

- [ ] **Step 5: Run the test to verify it fails, then passes after the refactor**

Run: `python -m pytest tests/test_app_routes.py -v`
Expected: PASS once Steps 1-2 are applied. If FAIL with template/JSON errors, fix the route to return the same shape the template expects.

- [ ] **Step 6: Manual smoke check**

Run: `python app.py` then load `http://127.0.0.1:5000/models` against a real Ollama. Expected: the page renders the local models exactly as before.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_routes.py
git commit -m "refactor: route all Ollama calls through registry + client"
```

---

## Task 4: Server management UI + active-server switcher

**Files:**
- Create: `templates/servers.html`
- Modify: `app.py` (add routes), `templates/base.html` (sidebar link + navbar switcher)
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `servers.*`, `OllamaClient.ping`.
- Produces routes: `GET /servers`, `POST /servers/add`, `POST /servers/<id>/edit`, `POST /servers/<id>/delete`, `POST /servers/active`.

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_app_routes.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app_routes.py -k server -v`
Expected: FAIL (404 — routes not defined)

- [ ] **Step 3: Add server-management routes to `app.py`**

```python
@app.route('/servers')
def servers_page():
    items = servers.list_servers()
    status = {}
    for s in items:
        status[s["id"]] = OllamaClient(s["base_url"]).ping()
    return render_template('servers.html', servers=items, status=status,
                           active_id=servers.get_active_id(session))


@app.route('/servers/add', methods=['POST'])
def servers_add():
    try:
        servers.add_server(request.form.get('name'), request.form.get('base_url'))
        flash("Server added", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/<server_id>/edit', methods=['POST'])
def servers_edit(server_id):
    try:
        servers.update_server(
            server_id,
            name=request.form.get('name'),
            base_url=request.form.get('base_url'),
            enabled='enabled' in request.form,
        )
        flash("Server updated", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/<server_id>/delete', methods=['POST'])
def servers_delete(server_id):
    try:
        servers.delete_server(server_id)
        flash("Server removed", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for('servers_page'))


@app.route('/servers/active', methods=['POST'])
def servers_set_active():
    servers.set_active(session, request.form.get('server_id'))
    return redirect(request.referrer or url_for('models'))
```

- [ ] **Step 4: Create `templates/servers.html`**

```html
{% extends "base.html" %}
{% block title %}Servers{% endblock %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
  <h1><i class="fas fa-server me-2"></i>Ollama Servers</h1>
</div>

<div class="card mb-4">
  <div class="card-header">Add a server</div>
  <div class="card-body">
    <form method="POST" action="{{ url_for('servers_add') }}" class="row g-2">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <div class="col-md-4">
        <input class="form-control" name="name" placeholder="Name (e.g. GPU box)" required>
      </div>
      <div class="col-md-6">
        <input class="form-control" name="base_url" placeholder="http://host:11434" required>
      </div>
      <div class="col-md-2 d-grid">
        <button class="btn btn-primary" type="submit"><i class="fas fa-plus me-1"></i>Add</button>
      </div>
    </form>
  </div>
</div>

<div class="table-responsive">
  <table class="table table-hover align-middle">
    <thead class="table-light">
      <tr><th>Name</th><th>Base URL</th><th>Status</th><th>Enabled</th><th>Active</th><th>Actions</th></tr>
    </thead>
    <tbody>
      {% for s in servers %}
      <tr>
        <td>{{ s.name }}</td>
        <td><code>{{ s.base_url }}</code></td>
        <td>
          {% if status[s.id] %}
            <span class="badge bg-success">online</span>
          {% else %}
            <span class="badge bg-secondary">offline</span>
          {% endif %}
        </td>
        <td>{{ 'Yes' if s.enabled else 'No' }}</td>
        <td>
          {% if s.id == active_id %}
            <span class="badge bg-dark">active</span>
          {% else %}
            <form method="POST" action="{{ url_for('servers_set_active') }}" class="d-inline">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
              <input type="hidden" name="server_id" value="{{ s.id }}">
              <button class="btn btn-sm btn-outline-dark" type="submit">Make active</button>
            </form>
          {% endif %}
        </td>
        <td>
          <button class="btn btn-sm btn-outline-primary" data-bs-toggle="collapse"
                  data-bs-target="#edit-{{ s.id }}"><i class="fas fa-edit"></i></button>
          <form method="POST" action="{{ url_for('servers_delete', server_id=s.id) }}" class="d-inline"
                onsubmit="return confirm('Remove {{ s.name }}?');">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button class="btn btn-sm btn-outline-danger" type="submit"><i class="fas fa-trash"></i></button>
          </form>
        </td>
      </tr>
      <tr class="collapse" id="edit-{{ s.id }}">
        <td colspan="6">
          <form method="POST" action="{{ url_for('servers_edit', server_id=s.id) }}" class="row g-2">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <div class="col-md-4"><input class="form-control" name="name" value="{{ s.name }}"></div>
            <div class="col-md-5"><input class="form-control" name="base_url" value="{{ s.base_url }}"></div>
            <div class="col-md-2 form-check d-flex align-items-center ms-2">
              <input class="form-check-input me-1" type="checkbox" name="enabled" {{ 'checked' if s.enabled }}>
              <label class="form-check-label">Enabled</label>
            </div>
            <div class="col-md-1 d-grid"><button class="btn btn-success" type="submit">Save</button></div>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Add sidebar link + navbar switcher to `base.html`**

In the sidebar nav list, add (next to the other items):
```html
<li class="nav-item">
  <a class="nav-link {% if request.path == '/servers' %}active{% endif %}" href="/servers">
    <i class="fas fa-server me-2"></i> Servers
  </a>
</li>
```
In the top navbar (right side), add a switcher. The base template must expose the server list — render it from a context processor. Add this to `app.py`:
```python
@app.context_processor
def inject_servers():
    return {
        "nav_servers": servers.get_enabled(),
        "nav_active_id": servers.get_active_id(session),
    }
```
Then in `base.html` navbar:
```html
<form method="POST" action="{{ url_for('servers_set_active') }}" class="d-flex align-items-center ms-auto">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <label class="text-nowrap me-2 small text-muted">Active server</label>
  <select name="server_id" class="form-select form-select-sm" onchange="this.form.submit()">
    {% for s in nav_servers %}
      <option value="{{ s.id }}" {{ 'selected' if s.id == nav_active_id }}>{{ s.name }}</option>
    {% endfor %}
  </select>
</form>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_routes.py -k server -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add app.py templates/servers.html templates/base.html tests/test_app_routes.py
git commit -m "feat: server management page and active-server switcher"
```

---

## Task 5: Per-server pull streaming + progress UI

**Files:**
- Modify: `app.py` (add `/pull/stream`; pull POST now renders the streaming page), `templates/pull_model.html`, `static/css/style.css`
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `servers.get_server`, `OllamaClient.pull`, `servers.get_enabled`.
- Produces route: `POST /pull/stream` (JSON `{server_id, model}`) → SSE per the event contract.

- [ ] **Step 1: Write a failing test for the stream endpoint**

Append to `tests/test_app_routes.py`:
```python
def test_pull_stream_forwards_and_terminates(client, monkeypatch):
    app_module, test_client = client
    import servers
    s = servers.list_servers()[0]

    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"pulling","total":100,"completed":50}'
            yield b'{"status":"success"}'

    class FakeClient:
        def __init__(self, *a, **k): pass
        def pull(self, model, stream=False): return FakeResp()

    monkeypatch.setattr(app_module, "OllamaClient", FakeClient)
    resp = test_client.post("/pull/stream", json={"server_id": s["id"], "model": "llama3.2"})
    body = resp.get_data(as_text=True)
    assert '"completed":50' in body
    assert '"done": true' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_routes.py -k pull_stream -v`
Expected: FAIL (404)

- [ ] **Step 3: Add `/pull/stream` and update the pull page route**

```python
@app.route('/pull/stream', methods=['POST'])
def pull_stream():
    data = request.get_json(silent=True) or {}
    server_id = data.get('server_id')
    model = data.get('model')
    server = servers.get_server(server_id)
    if not server or not model:
        return jsonify({"error": "server_id and model are required"}), 400

    def generate():
        try:
            resp = OllamaClient(server["base_url"]).pull(model, stream=True)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'error': f'HTTP {resp.status_code}'})}\n\n"
                return
            for line in resp.iter_lines():
                if line:
                    yield f"data: {line.decode('utf-8')}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')
```
Replace the existing `/pull` handler (currently `def pull_model()`, GET/POST) with a **GET-only** `pull_page` — the old blocking POST is superseded by client-side streaming. Task 8 extends this same function with `prefill_model`/`prefill_targets`; define it now as:
```python
@app.route('/pull')
def pull_page():
    return render_template('pull_model.html', servers=servers.get_enabled())
```
The sidebar link in `base.html` points at the static path `/pull`, so it needs no change.

- [ ] **Step 4: Rewrite `templates/pull_model.html`**

Replace the form body and add the progress + script. Full template:
```html
{% extends "base.html" %}
{% block title %}Pull Model{% endblock %}
{% block content %}
<h1 class="mb-4"><i class="fas fa-download me-2"></i>Pull Model</h1>
<input type="hidden" id="csrf_token" value="{{ csrf_token() }}">
<div class="card">
  <div class="card-body">
    <div class="mb-3">
      <label for="model_name" class="form-label">Model Name</label>
      <input type="text" class="form-control" id="model_name"
             placeholder="e.g., llama3.2, mistral:latest">
    </div>
    <div class="mb-3">
      <label class="form-label">Target servers</label>
      {% for s in servers %}
      <div class="form-check">
        <input class="form-check-input target-server" type="checkbox"
               id="srv-{{ s.id }}" value="{{ s.id }}" data-name="{{ s.name }}" checked>
        <label class="form-check-label" for="srv-{{ s.id }}">{{ s.name }} <small class="text-muted">{{ s.base_url }}</small></label>
      </div>
      {% endfor %}
    </div>
    <div class="d-flex justify-content-between">
      <a href="/models" class="btn btn-secondary">Cancel</a>
      <button id="pull-btn" class="btn btn-primary"><i class="fas fa-download me-2"></i>Pull Model</button>
    </div>
  </div>
</div>

<div id="progress-area" class="mt-4"></div>

{% block extra_js %}{% endblock %}
<script>
document.getElementById('pull-btn').addEventListener('click', function () {
  const model = document.getElementById('model_name').value.trim();
  if (!model) { alert('Enter a model name'); return; }
  const csrf = document.getElementById('csrf_token').value;
  const targets = Array.from(document.querySelectorAll('.target-server:checked'));
  if (!targets.length) { alert('Select at least one server'); return; }
  const area = document.getElementById('progress-area');
  area.innerHTML = '';
  let remaining = targets.length;

  targets.forEach(function (cb) {
    const sid = cb.value, name = cb.dataset.name;
    const card = document.createElement('div');
    card.className = 'card mb-2';
    card.innerHTML = `<div class="card-body py-2">
        <div class="d-flex justify-content-between"><strong>${name}</strong><span class="status small text-muted">starting…</span></div>
        <div class="progress mt-1" style="height:18px;">
          <div class="progress-bar" role="progressbar" style="width:0%">0%</div>
        </div>
      </div>`;
    area.appendChild(card);
    const bar = card.querySelector('.progress-bar');
    const status = card.querySelector('.status');

    fetch('/pull/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ server_id: sid, model: model })
    }).then(function (resp) {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      function read() {
        reader.read().then(function (r) {
          if (r.done) { finish(); return; }
          decoder.decode(r.value, { stream: true }).split('\n').forEach(function (line) {
            if (!line.startsWith('data: ')) return;
            try {
              const d = JSON.parse(line.slice(6));
              if (d.error) { status.textContent = 'error: ' + d.error; bar.classList.add('bg-danger'); return; }
              if (d.done) { return; }
              if (d.total && d.completed != null) {
                const pct = Math.floor(d.completed / d.total * 100);
                bar.style.width = pct + '%'; bar.textContent = pct + '%';
              }
              if (d.status) { status.textContent = d.status; }
            } catch (e) {}
          });
          read();
        });
      }
      read();
    }).catch(function (e) { status.textContent = 'error: ' + e.message; bar.classList.add('bg-danger'); finish(); });

    function finish() {
      if (!bar.classList.contains('bg-danger')) {
        bar.style.width = '100%'; bar.textContent = 'done'; bar.classList.add('bg-success');
        status.textContent = 'complete';
      }
      remaining -= 1;
      if (remaining === 0) {
        const done = document.createElement('div');
        done.className = 'mt-2';
        done.innerHTML = '<a href="/models" class="btn btn-outline-primary btn-sm">Back to Models</a>';
        area.appendChild(done);
      }
    }
  });
});
</script>
{% endblock %}
```

- [ ] **Step 5: Add progress-card styling to `static/css/style.css`**

```css
#progress-area .progress-bar.bg-success { color: #fff; }
#progress-area .card-body { font-size: 0.95rem; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_routes.py -k pull_stream -v`
Expected: PASS

- [ ] **Step 7: Manual check**

Run app; on `/pull`, enter a small model (e.g. `tinyllama`), keep both servers checked, click Pull. Expected: one progress bar per server advancing independently.

- [ ] **Step 8: Commit**

```bash
git add app.py templates/pull_model.html static/css/style.css tests/test_app_routes.py
git commit -m "feat: per-server pull progress streaming"
```

---

## Task 6: Create model with num_ctx + free-form params + per-server progress

**Files:**
- Modify: `app.py` (add `/create-model/stream`; keep/trim old `/create-model`), `templates/create_model.html`
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `build_create_payload` (Task 3), `servers.get_server`, `OllamaClient.create`, `servers.get_enabled`.
- Produces route: `POST /create-model/stream` (JSON `{server_id, model_name, from_model, system_prompt, template, quantize, num_ctx, parameters:[{key,value}]}`) → SSE.

- [ ] **Step 1: Write failing tests (payload assembly + stream)**

Append to `tests/test_app_routes.py`:
```python
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
    body = resp.get_data(as_text=True)
    assert '"creating"' in body
    assert '"done": true' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app_routes.py -k "create" -v`
Expected: FAIL (`build_create_payload` AttributeError or 404 on stream route)

- [ ] **Step 3: Add `/create-model/stream` route**

```python
@app.route('/create-model/stream', methods=['POST'])
def create_model_stream():
    data = request.get_json(silent=True) or {}
    server = servers.get_server(data.get('server_id'))
    if not server or not data.get('model_name'):
        return jsonify({"error": "server_id and model_name are required"}), 400
    payload = build_create_payload(data, stream=True)

    def generate():
        try:
            resp = OllamaClient(server["base_url"]).create(payload, stream=True)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'error': f'HTTP {resp.status_code}'})}\n\n"
                return
            for line in resp.iter_lines():
                if line:
                    yield f"data: {line.decode('utf-8')}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')
```
Pass `servers.get_enabled()` into the create page render: in the `/create` GET handler add `servers=servers.get_enabled()` to `render_template('create_model.html', ...)`.

- [ ] **Step 4: Update `templates/create_model.html` — add fields + target servers + per-server progress**

Inside the parameters section (after the Template textarea, before the Stream checkbox), add:
```html
<div class="mb-3">
  <label for="num_ctx" class="form-label">Context Size (num_ctx)</label>
  <input type="number" class="form-control" id="num_ctx" name="num_ctx"
         placeholder="Default: model defined" min="512" max="1048576">
  <div class="form-text">Common values: 2048, 4096, 8192, 16384, 32768</div>
</div>

<div class="mb-3">
  <label class="form-label">Additional parameters</label>
  <div id="param-rows"></div>
  <button type="button" class="btn btn-sm btn-outline-secondary mt-1" id="add-param">
    <i class="fas fa-plus me-1"></i>Add parameter
  </button>
  <div class="form-text">Arbitrary Ollama parameters, e.g. temperature = 0.7, stop = END</div>
</div>

<div class="mb-3">
  <label class="form-label">Target servers</label>
  {% for s in servers %}
  <div class="form-check">
    <input class="form-check-input target-server" type="checkbox"
           id="csrv-{{ s.id }}" value="{{ s.id }}" data-name="{{ s.name }}" checked>
    <label class="form-check-label" for="csrv-{{ s.id }}">{{ s.name }}</label>
  </div>
  {% endfor %}
</div>
```
Replace the existing streaming script's submit handler so it opens one `/create-model/stream` per checked server (mirror the pull page's per-server card/bar logic). Add this script at the end of the template's `extra_js`:
```html
<script>
document.getElementById('add-param').addEventListener('click', function () {
  const row = document.createElement('div');
  row.className = 'row g-2 mb-1 param-row';
  row.innerHTML = `<div class="col-5"><input class="form-control form-control-sm p-key" placeholder="key"></div>
    <div class="col-6"><input class="form-control form-control-sm p-val" placeholder="value"></div>
    <div class="col-1"><button type="button" class="btn btn-sm btn-outline-danger rm">&times;</button></div>`;
  document.getElementById('param-rows').appendChild(row);
  row.querySelector('.rm').addEventListener('click', () => row.remove());
});

document.getElementById('create-model-form').addEventListener('submit', function (e) {
  e.preventDefault();
  const csrf = document.querySelector('input[name="csrf_token"]').value;
  const targets = Array.from(document.querySelectorAll('.target-server:checked'));
  if (!targets.length) { alert('Select at least one server'); return; }
  const params = Array.from(document.querySelectorAll('.param-row')).map(r => ({
    key: r.querySelector('.p-key').value, value: r.querySelector('.p-val').value
  })).filter(p => p.key.trim());
  const base = {
    model_name: document.getElementById('model_name').value,
    creation_method: 'from_model',
    from_model: document.getElementById('from_model') ? document.getElementById('from_model').value : '',
    system_prompt: document.getElementById('system_prompt').value,
    template: document.getElementById('template').value,
    quantize: document.getElementById('quantize') ? document.getElementById('quantize').value : '',
    num_ctx: document.getElementById('num_ctx').value,
    parameters: params
  };
  const area = document.getElementById('progress-section');
  area.style.display = 'block';
  area.innerHTML = '<h5>Creation progress</h5>';
  let remaining = targets.length;

  targets.forEach(function (cb) {
    const card = document.createElement('div');
    card.className = 'card mb-2';
    card.innerHTML = `<div class="card-body py-2">
      <div class="d-flex justify-content-between"><strong>${cb.dataset.name}</strong><span class="status small text-muted">starting…</span></div>
      <div class="progress mt-1" style="height:18px;">
        <div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%">working…</div>
      </div></div>`;
    area.appendChild(card);
    const bar = card.querySelector('.progress-bar');
    const status = card.querySelector('.status');
    fetch('/create-model/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(Object.assign({ server_id: cb.value }, base))
    }).then(function (resp) {
      const reader = resp.body.getReader(), decoder = new TextDecoder();
      function read() {
        reader.read().then(function (r) {
          if (r.done) { finish(); return; }
          decoder.decode(r.value, { stream: true }).split('\n').forEach(function (line) {
            if (!line.startsWith('data: ')) return;
            try {
              const d = JSON.parse(line.slice(6));
              if (d.error) { status.textContent = 'error: ' + d.error; bar.className = 'progress-bar bg-danger'; bar.textContent='failed'; return; }
              if (d.status) status.textContent = d.status;
            } catch (e) {}
          });
          read();
        });
      }
      read();
    }).catch(function (e) { status.textContent = 'error: ' + e.message; bar.className='progress-bar bg-danger'; finish(); });

    function finish() {
      if (!bar.classList.contains('bg-danger')) {
        bar.className = 'progress-bar bg-success'; bar.style.width='100%'; bar.textContent='done';
        status.textContent = 'complete';
      }
      remaining -= 1;
      if (remaining === 0) {
        const done = document.createElement('div');
        done.innerHTML = '<a href="/models" class="btn btn-outline-primary btn-sm mt-2">Back to Models</a>';
        area.appendChild(done);
      }
    }
  });
});
</script>
```
Ensure the form has `id="create-model-form"` and the progress container `id="progress-section"` exists (rename the existing ones if needed). Remove the old single-server streaming script to avoid double submit.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_routes.py -k "create" -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Manual check**

Create a model `from` an existing base with `num_ctx = 8192` and a `temperature = 0.3` param, both servers checked. After completion, open the new model's detail page and confirm the parameter shows in the Modelfile.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/create_model.html tests/test_app_routes.py
git commit -m "feat: create models with num_ctx and free-form params across servers"
```

---

## Task 7: Broadcast delete with per-server results + target selection

**Files:**
- Modify: `app.py` (`delete_model` route), `templates/models.html` (delete modal sends target_ids)
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `servers.get_enabled`, `servers.get_server`, `OllamaClient.delete`.
- Produces helper: `broadcast_delete(model_name, target_ids) -> list[dict]` (each `{"name", "ok", "message"}`).

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_routes.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_routes.py -k broadcast_delete -v`
Expected: FAIL (`AttributeError: broadcast_delete`)

- [ ] **Step 3: Implement broadcast delete in `app.py`**

```python
def broadcast_delete(model_name, target_ids):
    results = []
    for sid in target_ids:
        server = servers.get_server(sid)
        if not server:
            continue
        try:
            resp = OllamaClient(server["base_url"]).delete(model_name)
            ok = resp.status_code == 200
            results.append({"name": server["name"], "ok": ok,
                            "message": "" if ok else f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"name": server["name"], "ok": False, "message": str(e)})
    return results


@app.route('/models/delete/<path:model_name>', methods=['POST'])
def delete_model(model_name):
    target_ids = request.form.getlist('target_ids')
    if not target_ids:
        target_ids = [s["id"] for s in servers.get_enabled()]
    for r in broadcast_delete(model_name, target_ids):
        if r["ok"]:
            flash(f"Deleted {model_name} from {r['name']}", "success")
        else:
            flash(f"Failed to delete {model_name} from {r['name']}: {r['message']}", "danger")
    return redirect(url_for('models'))
```

- [ ] **Step 4: Update the delete modal in `models.html` to send targets**

In the delete confirmation modal form, add a checkbox group (populated from the row's presence — defaults to servers that have the model). Add inside the modal body:
```html
<div id="delete-targets" class="mt-2"></div>
```
And in the delete-button JS handler, populate it (the merged row will carry `data-present` server ids in Task 8; for now render all enabled servers checked). Add to the existing delete-btn click handler:
```javascript
const present = (this.getAttribute('data-present') || '').split(',').filter(Boolean);
const box = document.getElementById('delete-targets');
box.innerHTML = '<label class="form-label small">Delete from:</label>';
window.__ALL_SERVERS__.forEach(function (s) {
  const checked = present.length === 0 || present.includes(s.id) ? 'checked' : '';
  box.innerHTML += `<div class="form-check"><input class="form-check-input" type="checkbox" name="target_ids" value="${s.id}" ${checked} form="deleteModelForm"><label class="form-check-label">${s.name}</label></div>`;
});
```
Define `window.__ALL_SERVERS__` near the top of the models template script:
```html
<script>window.__ALL_SERVERS__ = {{ servers | map(attribute='id') | list | tojson }}.map((id, i) => ({id: id, name: {{ servers | map(attribute='name') | list | tojson }}[i]}));</script>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_routes.py -k broadcast_delete -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app.py templates/models.html tests/test_app_routes.py
git commit -m "feat: broadcast model delete to selected servers"
```

---

## Task 8: Merged models view + drift banner + sync actions

**Files:**
- Modify: `app.py` (`models` route, optional running-models merge), `templates/models.html`
- Test: `tests/test_app_routes.py`

**Interfaces:**
- Consumes: `servers.get_enabled`, `OllamaClient.tags`.
- Produces helper: `merged_models(enabled) -> (models_list, server_status)` where each model dict has `name`, `size`, `details`, `modified_at`, `present_on` (list of server ids), `missing_on` (list of server records), `is_drift` (bool).

- [ ] **Step 1: Write failing test**

Append to `tests/test_app_routes.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_routes.py -k merged_models -v`
Expected: FAIL (`AttributeError: merged_models`)

- [ ] **Step 3: Implement `merged_models` and rewrite the `/models` route**

```python
def merged_models(enabled):
    merged = {}
    server_status = {}
    for s in enabled:
        try:
            data = OllamaClient(s["base_url"]).tags()
            server_status[s["id"]] = True
            for m in data.get("models", []):
                entry = merged.setdefault(m["name"], {
                    "name": m["name"], "size": m.get("size", 0),
                    "details": m.get("details", {}), "modified_at": m.get("modified_at"),
                    "present_on": [],
                })
                entry["present_on"].append(s["id"])
        except Exception:
            server_status[s["id"]] = False
    online_ids = {sid for sid, ok in server_status.items() if ok}
    models_list = list(merged.values())
    for m in models_list:
        m["missing_on"] = [s for s in enabled
                           if s["id"] in online_ids and s["id"] not in m["present_on"]]
        m["is_drift"] = len(m["missing_on"]) > 0
    return models_list, server_status


@app.route('/models')
def models():
    enabled = servers.get_enabled()
    models_list, server_status = merged_models(enabled)
    sort_by = request.args.get('sort', 'name')
    sort_order = request.args.get('order', 'asc')
    rev = sort_order == 'desc'
    if sort_by == 'name':
        models_list.sort(key=lambda x: x['name'].lower(), reverse=rev)
    elif sort_by == 'size':
        models_list.sort(key=lambda x: x.get('size', 0), reverse=rev)
    elif sort_by == 'modified':
        models_list.sort(key=lambda x: x.get('modified_at') or '', reverse=rev)
    drift_count = sum(1 for m in models_list if m['is_drift'])
    return render_template('models.html', models=models_list, servers=enabled,
                           server_status=server_status, drift_count=drift_count,
                           sort_by=sort_by, sort_order=sort_order)
```

- [ ] **Step 4: Update `models.html` — presence columns, drift banner, sync actions**

Add a drift banner above the table:
```html
{% if drift_count %}
<div class="alert alert-warning">
  <i class="fas fa-triangle-exclamation me-2"></i>
  {{ drift_count }} model(s) are not present on every online server.
</div>
{% endif %}
```
Add a presence header cell per server and a presence cell per row. In `<thead>`:
```html
{% for s in servers %}
<th class="text-center">{{ s.name }}{% if not server_status[s.id] %} <span class="badge bg-secondary">off</span>{% endif %}</th>
{% endfor %}
```
In each model `<tr>`, before the Actions cell:
```html
{% for s in servers %}
<td class="text-center">
  {% if s.id in model.present_on %}
    <i class="fas fa-check text-success"></i>
  {% elif server_status[s.id] %}
    <i class="fas fa-minus text-muted"></i>
  {% else %}
    <span class="text-muted">?</span>
  {% endif %}
</td>
{% endfor %}
```
In the Actions cell, add sync controls when drifted and wire the delete button's `data-present`:
```html
<button type="button" class="btn btn-sm btn-outline-danger delete-btn"
        data-model-name="{{ model.name }}"
        data-present="{{ model.present_on | join(',') }}"
        title="Delete model"><i class="fas fa-trash"></i></button>
{% if model.is_drift %}
<a class="btn btn-sm btn-outline-warning"
   href="{{ url_for('pull_page') }}?model={{ model.name | urlencode }}&targets={{ model.missing_on | map(attribute='id') | join(',') }}"
   title="Pull to servers missing it"><i class="fas fa-rotate"></i> Sync</a>
{% endif %}
```
The `/pull` GET handler reads optional `model` and `targets` query args to pre-fill and pre-check the pull page:
```python
@app.route('/pull')
def pull_page():
    return render_template('pull_model.html', servers=servers.get_enabled(),
                           prefill_model=request.args.get('model', ''),
                           prefill_targets=request.args.get('targets', '').split(','))
```
This extends the `pull_page` function defined in Task 5 (add the two `request.args` reads and the extra `render_template` kwargs). In `pull_model.html`, set the input value `value="{{ prefill_model }}"` and replace the always-`checked` on each target checkbox with `{{ 'checked' if (not prefill_targets or prefill_targets == [''] or s.id in prefill_targets) }}`.

- [ ] **Step 5: Optionally merge running-models across servers**

Update `/running-models`:
```python
@app.route('/running-models')
def running_models():
    rows = []
    for s in servers.get_enabled():
        try:
            data = OllamaClient(s["base_url"]).ps()
            for m in data.get("models", []):
                m["server_name"] = s["name"]
                rows.append(m)
        except Exception:
            continue
    return render_template('running_models.html', models=rows)
```
Add a `Server` column to `running_models.html` showing `model.server_name`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_routes.py -k merged_models -v`
Expected: PASS

- [ ] **Step 7: Full suite + manual check**

Run: `python -m pytest -v` (all green). Manually: with two servers holding different model sets, `/models` shows ✓/– per server, a drift banner, and a Sync button that opens the pull page pre-filled with the model and only the missing servers checked.

- [ ] **Step 8: Commit**

```bash
git add app.py templates/models.html templates/running_models.html tests/test_app_routes.py
git commit -m "feat: merged models view with drift detection and one-click sync"
```

---

## Task 9: Documentation + .env updates

**Files:**
- Modify: `README.md`, `.env.example`

- [ ] **Step 1: Update `.env.example`**

Append:
```
# WEBOLLAMA_SERVERS_FILE: path to the JSON file storing managed servers
# (default: servers.json next to app.py). OLLAMA_API_BASE below is now only
# used to seed the first server on first run; manage servers in the UI at /servers.
WEBOLLAMA_SERVERS_FILE=servers.json
```

- [ ] **Step 2: Update `README.md`**

Under Features, add: "Manage multiple Ollama servers from one panel; broadcast pull/delete/create with per-server progress; reconcile model drift; create models with custom context size and parameters."
Under Configuration, document `WEBOLLAMA_SERVERS_FILE` and note `OLLAMA_API_BASE` now seeds the first server. Add a short "Multiple Servers" section pointing to `/servers`.

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: document multi-server configuration"
```

---

## Self-Review

**1. Spec coverage:**
- Multiple servers managed in UI, JSON-persisted → Task 1 (`servers.py`) + Task 4 (UI). ✓
- Mutations default-all with per-action override → Task 5 (pull targets), Task 6 (create targets), Task 7 (delete targets). ✓
- Reconcile drift + one-click fix → Task 8 (merged view, drift banner, Sync button). ✓
- Per-server progress for pull + create → Task 5, Task 6. ✓
- `num_ctx` + free-form params → Task 6 (`build_create_payload`, form). ✓
- Global active-server switcher for single-server pages → Task 3 (`active_client`) + Task 4 (navbar). ✓
- Back-compat seeding from `OLLAMA_API_BASE` → Task 1. ✓
- `threaded=True` → Task 3. ✓
- Tests as first in repo → Tasks 1-8. ✓
- Docs → Task 9. ✓

**2. Placeholder scan:** No "TBD"/"add error handling"/"write tests for the above" left abstract — each step has concrete code or commands. Frontend "mirror the pull logic" steps include the full script rather than referring back. ✓

**3. Type consistency:** `normalize_url`, `build_create_payload(data, stream)`, `broadcast_delete(model_name, target_ids) -> list[{name,ok,message}]`, `merged_models(enabled) -> (models_list, server_status)`, presence keys `present_on`/`missing_on`/`is_drift` are used identically across tasks and templates. Route function `pull_page` is named consistently from Task 5 onward; Task 8 only extends it with prefill args (no rename). ✓
