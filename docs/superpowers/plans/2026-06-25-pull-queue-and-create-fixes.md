# Pull Queue, Create-Model Fix, and Navbar Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix create-model HTTP 400 and navbar dropdown clipping, and add a server-side pull queue that survives page navigation and supports cancellation.

**Architecture:** A new in-memory `pull_jobs` registry runs each pull in a per-server FIFO daemon worker thread; the browser polls `GET /pull/jobs` to render and resume the queue. Create-model gains a union base-model list + free-text combobox and an empty-`from` guard. The navbar select gets its chevron padding restored.

**Tech Stack:** Flask 3.1, `requests`, `threading`/`queue` (stdlib), Bootstrap 5.3, vanilla JS, pytest.

## Global Constraints

- Python 3.7+ compatible (no walrus-only patterns required; stdlib only for the registry).
- All POST endpoints are CSRF-protected; JS sends the `X-CSRFToken` header.
- Operator/user-supplied strings (server names, model names) interpolated into `innerHTML` MUST be escaped with `escapeHtml`.
- Multi-server reads tolerate offline servers silently (skip, never 500), matching `merged_models`.
- Run the full suite with `python3 -m pytest -q` from the repo root after each task; it must stay green.

---

### Task 1: Navbar dropdown clipping (Issue 2)

**Files:**
- Modify: `static/css/style.css` (append rules near the existing `.form-control, .form-select` block, ~line 478)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks (pure CSS).

- [ ] **Step 1: Add the chevron-padding and navbar-width rules**

Append to `static/css/style.css` (end of the Forms section, after the `.form-label` rule near line 497):

```css
/* The global .form-select padding override above removes the room Bootstrap
   reserves for the dropdown chevron; restore it so the chevron never paints
   over the option text (most visible in the navbar active-server select). */
.form-select {
  padding-right: 2.25rem;
  background-position: right 0.75rem center;
}
.form-select-sm {
  padding-right: 1.75rem;
}
.navbar .form-select-sm {
  min-width: 9rem;
  max-width: 16rem;
}

/* Pull-queue progress bars need their state colors back (the global
   .progress-bar override forces black with !important). */
#queue-area .progress-bar.bg-danger { background-color: var(--danger-color) !important; }
#queue-area .progress-bar.bg-success { background-color: var(--success-color) !important; }
#queue-area .progress-bar.bg-secondary { background-color: var(--secondary-color) !important; }
```

- [ ] **Step 2: Verify visually**

Run the app against the two test Ollama servers (or any seeded `servers.json`) and load any page. The navbar active-server select shows the full name (e.g. `Local A`) with the chevron clear of the text, and is at least 9rem wide. Confirm via a Playwright screenshot or browser that the text is no longer clipped.

- [ ] **Step 3: Commit**

```bash
git add static/css/style.css
git commit -m "fix: restore form-select chevron padding so navbar dropdown stops clipping"
```

---

### Task 2: Create-model from-fix — union base models + empty-from guard (Issue 1)

**Files:**
- Modify: `app.py` (add `union_model_names`, change `create_model_page`, add guard in `create_model_stream`)
- Modify: `templates/create_model.html` (replace `<select id="from_model">` with a combobox + `<datalist>`)
- Test: `tests/test_app_routes.py` (add empty-from 400 test)

**Interfaces:**
- Consumes: `servers.get_enabled()`, `OllamaClient(base_url).tags()`, `build_create_payload`.
- Produces: `union_model_names(enabled) -> list[str]` (sorted, deduped). `create_model_page` renders `create_model.html` with `model_names` (list of strings) instead of `models`.

- [ ] **Step 1: Write the failing test for the empty-`from` guard**

Add to `tests/test_app_routes.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_app_routes.py::test_create_stream_rejects_empty_from -q`
Expected: FAIL (currently returns 200 and streams, or 400 without the "base model" message).

- [ ] **Step 3: Add `union_model_names` and the guard in `app.py`**

Add this helper above `create_model_page` (near the other create helpers, ~line 275):

```python
def union_model_names(enabled):
    """Deduped, sorted model names across all reachable enabled servers."""
    names = set()
    for s in enabled:
        try:
            data = OllamaClient(s["base_url"]).tags()
            for m in data.get("models", []):
                names.add(m["name"])
        except Exception:
            continue
    return sorted(names)
```

Replace the body of `create_model_page` (the `GET /create` route) with:

```python
@app.route('/create', methods=['GET'])
def create_model_page():
    enabled = servers.get_enabled()
    if not enabled:
        flash("No active server configured. Add or enable a server on the Servers page.", "danger")
    return render_template('create_model.html',
                           model_names=union_model_names(enabled),
                           servers=enabled)
```

In `create_model_stream`, after the `if not server or not data.get('model_name')` check and before the `try: payload = ...`, add:

```python
    method = data.get('creation_method', 'from_model')
    if method == 'from_model' and not (data.get('from_model') or '').strip():
        return jsonify({"error": "Select or enter a base model"}), 400
```

- [ ] **Step 4: Update the template combobox**

In `templates/create_model.html`, replace the entire `<select class="form-select" id="from_model" ...> ... </select>` block (the one inside `#from_model_section`, currently lines ~181-189) with:

```html
                                <input class="form-control" list="base-model-list"
                                       id="from_model" name="from_model" required
                                       placeholder="e.g. llama3.2 (type any model name)">
                                <datalist id="base-model-list">
                                    {% for name in model_names %}
                                    <option value="{{ name }}">
                                    {% endfor %}
                                </datalist>
                                <div class="form-text">Pick an existing model or type any model name; target servers pull it if missing.</div>
```

(Delete the now-duplicate `<div class="form-text">The existing model to use as a base</div>` line directly below the old select.)

- [ ] **Step 5: Run the new test and the full suite**

Run: `python3 -m pytest tests/test_app_routes.py::test_create_stream_rejects_empty_from -q && python3 -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/create_model.html tests/test_app_routes.py
git commit -m "fix: create-model uses union base-model list + free text, guards empty from"
```

---

### Task 3: `pull_jobs.py` registry + unit tests (Issue 3 core)

**Files:**
- Create: `pull_jobs.py`
- Create: `tests/test_pull_jobs.py`

**Interfaces:**
- Consumes: `ollama_client.OllamaClient(base_url).pull(model, stream=True) -> requests.Response` (has `.status_code`, `.iter_lines()`, `.close()`).
- Produces:
  - `enqueue(server: dict, model: str) -> dict | None` — `server` has `id, name, base_url`; returns the public job dict (or the existing active job for the same server+model; `None` if model blank).
  - `snapshot() -> list[dict]` — public job dicts sorted by `seq`; no keys starting with `_`.
  - `cancel(job_id: str) -> bool`.
  - `clear_finished() -> None`.
  - `reset() -> None` — test helper that clears registry state.
  - Public job keys: `id, server_id, server_name, model, state, total, completed, status, error, seq`. `state ∈ {queued, running, success, error, canceled}`.

- [ ] **Step 1: Write the module `pull_jobs.py`**

```python
import copy
import itertools
import json
import queue
import threading
import uuid

from ollama_client import OllamaClient

_LOCK = threading.RLock()
_JOBS = {}        # job_id -> internal job dict (includes _cancel, _base_url)
_QUEUES = {}      # server_id -> queue.Queue of job_id
_WORKERS = {}     # server_id -> Thread
_SEQ = itertools.count(1)

ACTIVE = ("queued", "running")
FINISHED = ("success", "error", "canceled")


def _public(job):
    return {k: v for k, v in job.items() if not k.startswith("_")}


def snapshot():
    with _LOCK:
        ordered = sorted(_JOBS.values(), key=lambda j: j["seq"])
        return [copy.deepcopy(_public(j)) for j in ordered]


def _find_active(server_id, model):
    for j in _JOBS.values():
        if j["server_id"] == server_id and j["model"] == model and j["state"] in ACTIVE:
            return j
    return None


def enqueue(server, model):
    model = (model or "").strip()
    if not model:
        return None
    with _LOCK:
        existing = _find_active(server["id"], model)
        if existing:
            return _public(existing)
        job = {
            "id": uuid.uuid4().hex,
            "server_id": server["id"],
            "server_name": server.get("name", server["id"]),
            "model": model,
            "state": "queued",
            "total": 0,
            "completed": 0,
            "status": "queued",
            "error": None,
            "seq": next(_SEQ),
            "_cancel": threading.Event(),
            "_base_url": server["base_url"],
        }
        _JOBS[job["id"]] = job
        q = _QUEUES.get(server["id"])
        if q is None:
            q = queue.Queue()
            _QUEUES[server["id"]] = q
        q.put(job["id"])
        _ensure_worker(server["id"])
        return _public(job)


def _ensure_worker(server_id):
    t = _WORKERS.get(server_id)
    if t is None or not t.is_alive():
        t = threading.Thread(target=_worker, args=(server_id,), daemon=True)
        _WORKERS[server_id] = t
        t.start()


def _worker(server_id):
    q = _QUEUES[server_id]
    while True:
        job_id = q.get()
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                continue
            if job["_cancel"].is_set():
                job["state"] = "canceled"
                job["status"] = "canceled"
                continue
            job["state"] = "running"
            job["status"] = "starting"
            base_url = job["_base_url"]
            model = job["model"]
            cancel = job["_cancel"]
        _run_pull(job_id, base_url, model, cancel)


def _run_pull(job_id, base_url, model, cancel):
    try:
        resp = OllamaClient(base_url).pull(model, stream=True)
        if getattr(resp, "status_code", 0) != 200:
            _fail(job_id, "HTTP %s" % getattr(resp, "status_code", "error"))
            return
        for line in resp.iter_lines():
            if cancel.is_set():
                try:
                    resp.close()
                except Exception:
                    pass
                _set_state(job_id, "canceled", status="canceled")
                return
            if not line:
                continue
            try:
                d = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if d.get("error"):
                _fail(job_id, d["error"])
                return
            _update_progress(job_id, d)
        _set_state(job_id, "success", status="success", completed_full=True)
    except Exception as e:
        _fail(job_id, str(e))


def _update_progress(job_id, d):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        if d.get("status"):
            job["status"] = d["status"]
        if d.get("total"):
            job["total"] = d["total"]
        if d.get("completed") is not None:
            job["completed"] = d["completed"]


def _fail(job_id, msg):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["state"] = "error"
        job["error"] = msg
        job["status"] = "error"


def _set_state(job_id, state, status=None, completed_full=False):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["state"] = state
        if status:
            job["status"] = status
        if completed_full and job["total"]:
            job["completed"] = job["total"]


def cancel(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["state"] in FINISHED:
            return False
        job["_cancel"].set()
        if job["state"] == "queued":
            job["state"] = "canceled"
            job["status"] = "canceled"
        return True


def clear_finished():
    with _LOCK:
        for jid in [j["id"] for j in list(_JOBS.values()) if j["state"] in FINISHED]:
            del _JOBS[jid]


def reset():
    """Test helper: clear registry state. Orphaned daemon workers idle on old queues."""
    with _LOCK:
        _JOBS.clear()
        _QUEUES.clear()
        _WORKERS.clear()
```

- [ ] **Step 2: Write the unit tests `tests/test_pull_jobs.py`**

```python
import threading
import time

import pytest

import pull_jobs


@pytest.fixture(autouse=True)
def fresh():
    pull_jobs.reset()
    yield
    pull_jobs.reset()


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def server(id="s1", name="A", base_url="http://x"):
    return {"id": id, "name": name, "base_url": base_url}


def state_of(job_id):
    for j in pull_jobs.snapshot():
        if j["id"] == job_id:
            return j
    return None


def test_pull_runs_and_reports_progress(monkeypatch):
    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"pulling","total":100,"completed":40}'
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    job = pull_jobs.enqueue(server(), "llama3.2")
    assert wait_for(lambda: state_of(job["id"])["state"] == "success")
    s = state_of(job["id"])
    assert s["total"] == 100
    assert s["completed"] == 100


def test_blank_model_returns_none():
    assert pull_jobs.enqueue(server(), "   ") is None


def test_snapshot_hides_internal_keys(monkeypatch):
    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)
    job = pull_jobs.enqueue(server(), "m")
    wait_for(lambda: state_of(job["id"])["state"] == "success")
    snap = state_of(job["id"])
    assert not any(k.startswith("_") for k in snap)


def test_dedup_same_server_model(monkeypatch):
    gate = threading.Event()
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

    j1 = pull_jobs.enqueue(server(), "m")
    assert wait_for(lambda: state_of(j1["id"])["state"] == "running")
    j2 = pull_jobs.enqueue(server(), "m")
    assert j2["id"] == j1["id"]
    assert len([j for j in pull_jobs.snapshot() if j["model"] == "m"]) == 1
    gate.set()


def test_sequential_per_server(monkeypatch):
    gates = {"a": threading.Event(), "b": threading.Event()}
    class FakeResp:
        def __init__(self, model):
            self.model = model
            self.status_code = 200
        def iter_lines(self):
            gates[self.model].wait(2)
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp(model)
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    j1 = pull_jobs.enqueue(server(), "a")
    j2 = pull_jobs.enqueue(server(), "b")
    assert wait_for(lambda: state_of(j1["id"])["state"] == "running")
    time.sleep(0.1)
    assert state_of(j2["id"])["state"] == "queued"
    gates["a"].set()
    assert wait_for(lambda: state_of(j1["id"])["state"] == "success")
    assert wait_for(lambda: state_of(j2["id"])["state"] == "running")
    gates["b"].set()
    assert wait_for(lambda: state_of(j2["id"])["state"] == "success")


def test_parallel_across_servers(monkeypatch):
    gate = threading.Event()
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

    j1 = pull_jobs.enqueue(server("s1", "A", "http://a"), "m")
    j2 = pull_jobs.enqueue(server("s2", "B", "http://b"), "m")
    assert wait_for(lambda: state_of(j1["id"])["state"] == "running"
                    and state_of(j2["id"])["state"] == "running")
    gate.set()


def test_cancel_queued(monkeypatch):
    gate = threading.Event()
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

    j1 = pull_jobs.enqueue(server(), "a")
    j2 = pull_jobs.enqueue(server(), "b")
    assert wait_for(lambda: state_of(j1["id"])["state"] == "running")
    assert pull_jobs.cancel(j2["id"]) is True
    assert state_of(j2["id"])["state"] == "canceled"
    gate.set()


def test_cancel_running(monkeypatch):
    gate = threading.Event()
    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"pulling","total":100,"completed":1}'
            gate.wait(2)
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    j = pull_jobs.enqueue(server(), "a")
    assert wait_for(lambda: state_of(j["id"])["state"] == "running"
                    and state_of(j["id"])["completed"] == 1)
    assert pull_jobs.cancel(j["id"]) is True
    gate.set()
    assert wait_for(lambda: state_of(j["id"])["state"] == "canceled")


def test_error_on_non_200(monkeypatch):
    class FakeResp:
        status_code = 500
        def iter_lines(self): return iter([])
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    j = pull_jobs.enqueue(server(), "a")
    assert wait_for(lambda: state_of(j["id"])["state"] == "error")
    assert "500" in state_of(j["id"])["error"]


def test_error_in_stream(monkeypatch):
    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"error":"pull failed: not found"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    j = pull_jobs.enqueue(server(), "a")
    assert wait_for(lambda: state_of(j["id"])["state"] == "error")
    assert "not found" in state_of(j["id"])["error"]


def test_clear_finished_removes_only_done(monkeypatch):
    class FakeResp:
        status_code = 200
        def iter_lines(self):
            yield b'{"status":"success"}'
        def close(self): pass
    class FakeClient:
        def __init__(self, base_url): pass
        def pull(self, model, stream=True): return FakeResp()
    monkeypatch.setattr(pull_jobs, "OllamaClient", FakeClient)

    j = pull_jobs.enqueue(server(), "a")
    assert wait_for(lambda: state_of(j["id"])["state"] == "success")
    pull_jobs.clear_finished()
    assert state_of(j["id"]) is None
```

- [ ] **Step 3: Run the tests**

Run: `python3 -m pytest tests/test_pull_jobs.py -q`
Expected: PASS (all). If a timing test flakes, the `wait_for` timeout (3s) is generous; re-run.

- [ ] **Step 4: Commit**

```bash
git add pull_jobs.py tests/test_pull_jobs.py
git commit -m "feat: in-memory per-server pull job registry with cancel"
```

---

### Task 4: Pull routes — enqueue/jobs/cancel/clear, remove /pull/stream (Issue 3 wiring)

**Files:**
- Modify: `app.py` (import `pull_jobs`; remove `pull_stream` route; add four routes)
- Modify: `tests/test_app_routes.py` (remove old `/pull/stream` test; add enqueue/jobs/cancel tests)

**Interfaces:**
- Consumes: `pull_jobs.enqueue/snapshot/cancel/clear_finished`, `servers.get_enabled/get_server`.
- Produces: `POST /pull/enqueue {model, server_ids?} -> {jobs:[...]}`; `GET /pull/jobs -> {jobs:[...]}`; `POST /pull/cancel/<job_id> -> {ok}`; `POST /pull/clear -> {ok}`.

- [ ] **Step 1: Replace the old test with new route tests**

In `tests/test_app_routes.py`, delete `test_pull_stream_forwards_and_terminates` entirely. Add:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_app_routes.py::test_pull_enqueue_and_jobs -q`
Expected: FAIL (404 — route does not exist yet).

- [ ] **Step 3: Wire the routes in `app.py`**

Add `import pull_jobs` next to `import servers` (top of file, ~line 9).

Delete the entire `pull_stream` function and its `@app.route('/pull/stream', methods=['POST'])` decorator (currently ~lines 224-246).

Add these routes (place them right after the `pull_page` route, ~line 222):

```python
@app.route('/pull/enqueue', methods=['POST'])
def pull_enqueue():
    data = request.get_json(silent=True) or {}
    model = (data.get('model') or '').strip()
    if not model:
        return jsonify({"error": "model is required"}), 400
    ids = data.get('server_ids') or [s["id"] for s in servers.get_enabled()]
    jobs = []
    for sid in ids:
        s = servers.get_server(sid)
        if not s:
            continue
        job = pull_jobs.enqueue(s, model)
        if job:
            jobs.append(job)
    if not jobs:
        return jsonify({"error": "no valid target servers"}), 400
    return jsonify({"jobs": jobs})


@app.route('/pull/jobs')
def pull_jobs_list():
    return jsonify({"jobs": pull_jobs.snapshot()})


@app.route('/pull/cancel/<job_id>', methods=['POST'])
def pull_cancel(job_id):
    return jsonify({"ok": pull_jobs.cancel(job_id)})


@app.route('/pull/clear', methods=['POST'])
def pull_clear():
    pull_jobs.clear_finished()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run the new tests and the full suite**

Run: `python3 -m pytest tests/test_app_routes.py -q && python3 -m pytest -q`
Expected: PASS; full suite green (the old `/pull/stream` test is gone).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_routes.py
git commit -m "feat: pull enqueue/jobs/cancel/clear routes; drop browser-streamed /pull/stream"
```

---

### Task 5: Pull page rewrite — enqueue + poll + cancel UI (Issue 3 frontend)

**Files:**
- Modify: `templates/pull_model.html` (full rewrite of content + JS)

**Interfaces:**
- Consumes: `POST /pull/enqueue`, `GET /pull/jobs`, `POST /pull/cancel/<id>`, `POST /pull/clear`; `prefill_model`, `prefill_targets`, `servers` (unchanged from `pull_page`).
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite `templates/pull_model.html`**

Replace the whole file with:

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
             placeholder="e.g., llama3.2, mistral:latest"
             value="{{ prefill_model }}">
    </div>
    <div class="mb-3">
      <label class="form-label">Target servers</label>
      {% for s in servers %}
      <div class="form-check">
        <input class="form-check-input target-server" type="checkbox"
               id="srv-{{ s.id }}" value="{{ s.id }}" data-name="{{ s.name }}"
               {{ 'checked' if (not prefill_targets or prefill_targets == [''] or s.id in prefill_targets) }}>
        <label class="form-check-label" for="srv-{{ s.id }}">{{ s.name }} <small class="text-muted">{{ s.base_url }}</small></label>
      </div>
      {% endfor %}
    </div>
    <div class="d-flex justify-content-between">
      <a href="/models" class="btn btn-secondary">Back to Models</a>
      <button id="pull-btn" class="btn btn-primary"><i class="fas fa-download me-2"></i>Add to queue</button>
    </div>
  </div>
</div>

<div class="d-flex justify-content-between align-items-center mt-4 mb-2">
  <h5 class="mb-0">Pull queue</h5>
  <button id="clear-btn" class="btn btn-sm btn-outline-secondary">Clear finished</button>
</div>
<div id="queue-area"><p class="text-muted small">No pulls yet.</p></div>
{% endblock %}

{% block extra_js %}
<script>
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

var CSRF = document.getElementById('csrf_token').value;

var BADGE = {
  queued: 'bg-secondary', running: 'bg-dark',
  success: 'bg-success', error: 'bg-danger', canceled: 'bg-secondary'
};

function pct(j) {
  if (j.total) return Math.floor(j.completed / j.total * 100);
  return j.state === 'success' ? 100 : 0;
}

function barClass(j) {
  if (j.state === 'error') return 'progress-bar bg-danger';
  if (j.state === 'success') return 'progress-bar bg-success';
  if (j.state === 'canceled') return 'progress-bar bg-secondary';
  if (j.state === 'running') return 'progress-bar progress-bar-striped progress-bar-animated';
  return 'progress-bar';
}

function render(jobs) {
  var area = document.getElementById('queue-area');
  if (!jobs.length) { area.innerHTML = '<p class="text-muted small">No pulls yet.</p>'; return; }
  area.innerHTML = jobs.map(function (j) {
    var p = pct(j);
    var active = (j.state === 'queued' || j.state === 'running');
    var label = j.state === 'error'
      ? ('error: ' + escapeHtml(j.error || ''))
      : escapeHtml(j.status || j.state);
    var btn = active
      ? '<button class="btn btn-sm btn-outline-danger cancel-btn" data-id="' + escapeHtml(j.id) + '">Cancel</button>'
      : '';
    return '<div class="card mb-2"><div class="card-body py-2">'
      + '<div class="d-flex justify-content-between align-items-center">'
      + '<div><strong>' + escapeHtml(j.model) + '</strong> '
      + '<span class="text-muted small">' + escapeHtml(j.server_name) + '</span></div>'
      + '<div class="d-flex align-items-center gap-2">'
      + '<span class="badge ' + (BADGE[j.state] || 'bg-secondary') + '">' + escapeHtml(j.state) + '</span>'
      + btn + '</div></div>'
      + '<div class="progress mt-1" style="height:18px;"><div class="' + barClass(j) + '" style="width:' + p + '%">' + p + '%</div></div>'
      + '<div class="small text-muted mt-1">' + label + '</div>'
      + '</div></div>';
  }).join('');
  Array.prototype.forEach.call(area.querySelectorAll('.cancel-btn'), function (b) {
    b.addEventListener('click', function () { cancelJob(b.dataset.id); });
  });
}

function refresh() {
  return fetch('/pull/jobs').then(function (r) { return r.json(); })
    .then(function (d) { render(d.jobs || []); }).catch(function () {});
}

function cancelJob(id) {
  fetch('/pull/cancel/' + encodeURIComponent(id),
    { method: 'POST', headers: { 'X-CSRFToken': CSRF } }).then(refresh);
}

document.getElementById('pull-btn').addEventListener('click', function () {
  var model = document.getElementById('model_name').value.trim();
  if (!model) { alert('Enter a model name'); return; }
  var ids = Array.prototype.slice.call(document.querySelectorAll('.target-server:checked'))
    .map(function (c) { return c.value; });
  if (!ids.length) { alert('Select at least one server'); return; }
  fetch('/pull/enqueue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
    body: JSON.stringify({ model: model, server_ids: ids })
  }).then(function (r) { return r.json(); })
    .then(function (d) { if (d.error) alert(d.error); refresh(); })
    .catch(function () {});
});

document.getElementById('clear-btn').addEventListener('click', function () {
  fetch('/pull/clear', { method: 'POST', headers: { 'X-CSRFToken': CSRF } }).then(refresh);
});

refresh();
setInterval(refresh, 1000);
</script>
{% endblock %}
```

- [ ] **Step 2: Manual end-to-end verification**

With two Ollama servers seeded and the app running:
1. Load `/pull`, enter a small model (e.g. `smollm:135m`), keep both servers checked, click **Add to queue**. Two cards appear and progress advances.
2. Navigate to `/models`, then back to `/pull` — the queue cards are still there with live progress (resume works).
3. Enqueue a second model; on each server the second waits (`queued`) until the first finishes (sequential per server).
4. Click **Cancel** on a running/queued job — it flips to `canceled`.
5. Click **Clear finished** — finished/canceled cards disappear; active ones remain.

- [ ] **Step 3: Run the full suite (no regressions)**

Run: `python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add templates/pull_model.html
git commit -m "feat: pull page enqueues to the server-side queue, polls + resumes, cancel/clear"
```

---

## Self-Review

**Spec coverage:**
- Issue 1 union base-model list + combobox + empty-from guard → Task 2. ✓
- Issue 2 chevron padding + navbar width → Task 1. ✓
- Issue 3 `pull_jobs` registry (per-server FIFO, parallel across servers, cancel, dedup, in-memory) → Task 3. ✓
- Issue 3 routes (enqueue/jobs/cancel/clear, remove `/pull/stream`) → Task 4. ✓
- Issue 3 frontend (Add to queue, poll/resume, cancel, clear, escapeHtml) → Task 5. ✓
- Queue-bar state colors (defeat the `!important` black override) → Task 1 CSS. ✓
- Tests: registry unit tests + route tests + remove old `/pull/stream` test → Tasks 3, 4. ✓

**Placeholder scan:** none — every step shows full code/commands.

**Type consistency:** `enqueue(server, model)`, `snapshot()`, `cancel(job_id)`, `clear_finished()`, `reset()` and the public job keys (`id, server_id, server_name, model, state, total, completed, status, error, seq`) are used identically in Tasks 3, 4, 5. Routes return `{jobs}` / `{ok}` consistently with the frontend's `d.jobs` / cancel calls. `model_names` (list of strings) is produced in Task 2's `create_model_page` and consumed by the Task 2 datalist.
