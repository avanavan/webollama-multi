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
