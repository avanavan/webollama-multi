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
