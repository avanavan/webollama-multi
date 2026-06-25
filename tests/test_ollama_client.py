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
