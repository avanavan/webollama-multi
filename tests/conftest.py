import os
import pytest


@pytest.fixture
def servers_file(tmp_path, monkeypatch):
    """Point servers.py at a throwaway JSON file and a known seed URL."""
    path = tmp_path / "servers.json"
    monkeypatch.setenv("WEBOLLAMA_SERVERS_FILE", str(path))
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    return path
