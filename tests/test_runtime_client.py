"""runtime.client -- the HTTP-side counterpart to runtime.hermes.run(),
against a mocked httpx transport. No real network call, no real server.
"""

from __future__ import annotations

import httpx
import pytest

from runtime.client import RuntimeClientError, run
from runtime.hermes import HermesRequest


def _client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_posts_the_request_and_returns_a_hermes_result(monkeypatch, tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={
            "response": "Here's the PRD.", "usage": {"failed": False, "input_tokens": 42},
            "exit_code": 0, "session_id": "sess-1", "failed": False,
        })

    monkeypatch.setattr(
        httpx, "post",
        lambda url, json, timeout: _client_with(handler).post(url, json=json),
    )

    request = HermesRequest(
        prompt="hi", home_dir=tmp_path, provider="openrouter", model="openai/gpt-4o-mini",
    )
    result = run(request, base_url="http://agent-runtime:8000")

    assert result.response == "Here's the PRD."
    assert result.session_id == "sess-1"
    assert result.failed is False
    assert captured["url"] == "http://agent-runtime:8000/run"
    assert b'"prompt":"hi"' in captured["body"] or b'"prompt": "hi"' in captured["body"]


def test_run_passes_usage_file_and_resume_session_id(monkeypatch, tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib
        captured["payload"] = jsonlib.loads(request.read())
        return httpx.Response(200, json={
            "response": "ok", "usage": {"failed": False}, "exit_code": 0,
            "session_id": "sess-1", "failed": False,
        })

    monkeypatch.setattr(
        httpx, "post",
        lambda url, json, timeout: _client_with(handler).post(url, json=json),
    )

    request = HermesRequest(
        prompt="continue", home_dir=tmp_path, provider="openrouter", model="m",
        resume_session_id="sess-1",
    )
    run(request, usage_file=tmp_path / "usage.json", base_url="http://x")

    assert captured["payload"]["resume_session_id"] == "sess-1"
    assert captured["payload"]["usage_file"] == str(tmp_path / "usage.json")


def test_run_raises_on_network_error(monkeypatch, tmp_path):
    def raise_error(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", raise_error)

    request = HermesRequest(prompt="hi", home_dir=tmp_path, provider="p", model="m")
    with pytest.raises(RuntimeClientError, match="could not reach"):
        run(request, base_url="http://agent-runtime:8000")


def test_run_raises_on_server_error_status(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="failed to launch hermes: not found")

    monkeypatch.setattr(
        httpx, "post",
        lambda url, json, timeout: _client_with(handler).post(url, json=json),
    )

    request = HermesRequest(prompt="hi", home_dir=tmp_path, provider="p", model="m")
    with pytest.raises(RuntimeClientError, match="502"):
        run(request, base_url="http://agent-runtime:8000")


def test_run_a_failed_turn_is_not_a_client_error(monkeypatch, tmp_path):
    """A turn that ran but failed comes back as HTTP 200 with failed data in
    the body -- the client must not raise for this, only for transport
    problems."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "response": "", "usage": {"failed": True, "failure": "boom"},
            "exit_code": 1, "session_id": None, "failed": True,
        })

    monkeypatch.setattr(
        httpx, "post",
        lambda url, json, timeout: _client_with(handler).post(url, json=json),
    )

    request = HermesRequest(prompt="hi", home_dir=tmp_path, provider="p", model="m")
    result = run(request, base_url="http://agent-runtime:8000")

    assert result.failed is True
