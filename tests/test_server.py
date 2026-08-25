"""runtime/server.py -- the HTTP boundary, exercised via FastAPI's TestClient.

hermes_run is monkeypatched on the server module the same way every other
caller mocks it: no subprocess, no network call to a real Hermes binary.
What's under test is the transport (request parsing, response shape, error
mapping), not the invocation contract itself -- that's runtime/hermes.py's
own test suite's job.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import runtime.server as server_module
from runtime.hermes import HermesInvocationError, HermesResult
from runtime.server import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_returns_the_response_and_usage(monkeypatch, tmp_path):
    captured = {}

    def fake_run(request, usage_file=None):
        captured["request"] = request
        captured["usage_file"] = usage_file
        return HermesResult(
            response="Here's the PRD.", usage={"failed": False, "input_tokens": 42},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(server_module, "hermes_run", fake_run)

    response = client.post("/run", json={
        "prompt": "Build a habit tracker.",
        "home_dir": str(tmp_path),
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Here's the PRD."
    assert body["usage"] == {"failed": False, "input_tokens": 42}
    assert body["session_id"] == "sess-1"
    assert body["failed"] is False

    assert str(captured["request"].home_dir) == str(tmp_path)
    assert captured["request"].provider == "openrouter"
    assert captured["request"].resume_session_id is None
    assert captured["usage_file"] is None


def test_run_passes_through_resume_session_id(monkeypatch, tmp_path):
    captured = {}

    def fake_run(request, usage_file=None):
        captured["request"] = request
        return HermesResult(
            response="Revised.", usage={"failed": False}, exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(server_module, "hermes_run", fake_run)

    client.post("/run", json={
        "prompt": "Make it support two users.",
        "home_dir": str(tmp_path),
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
        "resume_session_id": "sess-1",
    })

    assert captured["request"].resume_session_id == "sess-1"
    assert captured["request"].is_continuation is True


def test_run_passes_through_usage_file_path(monkeypatch, tmp_path):
    captured = {}
    usage_path = tmp_path / "usage.json"

    def fake_run(request, usage_file=None):
        captured["usage_file"] = usage_file
        return HermesResult(response="ok", usage={"failed": False}, exit_code=0, session_id="s")

    monkeypatch.setattr(server_module, "hermes_run", fake_run)

    client.post("/run", json={
        "prompt": "hi", "home_dir": str(tmp_path), "provider": "openrouter",
        "model": "openai/gpt-4o-mini", "usage_file": str(usage_path),
    })

    assert captured["usage_file"] == usage_path


def test_run_reports_a_failed_turn_without_an_http_error(monkeypatch, tmp_path):
    """A turn that ran but failed (bad response, non-zero exit) is still a
    successful HTTP call -- the failure is data in the response body, not a
    transport error. HTTPException is reserved for the binary not starting
    at all (see the next test)."""
    monkeypatch.setattr(server_module, "hermes_run", lambda request, usage_file=None: HermesResult(
        response="", usage={"failed": True, "failure": "boom"}, exit_code=1, session_id=None,
    ))

    response = client.post("/run", json={
        "prompt": "hi", "home_dir": str(tmp_path), "provider": "openrouter", "model": "m",
    })

    assert response.status_code == 200
    assert response.json()["failed"] is True


def test_run_maps_invocation_error_to_502(monkeypatch, tmp_path):
    def raise_invocation_error(request, usage_file=None):
        raise HermesInvocationError("failed to launch hermes: not found")

    monkeypatch.setattr(server_module, "hermes_run", raise_invocation_error)

    response = client.post("/run", json={
        "prompt": "hi", "home_dir": str(tmp_path), "provider": "openrouter", "model": "m",
    })

    assert response.status_code == 502
    assert "not found" in response.json()["detail"]


def test_run_rejects_a_malformed_request():
    response = client.post("/run", json={"prompt": "hi"})  # missing required fields
    assert response.status_code == 422
