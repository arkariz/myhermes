"""runtime/server.py -- the HTTP boundary, exercised via FastAPI's TestClient.

hermes_run is monkeypatched on the server module the same way every other
caller mocks it: no subprocess, no network call to a real Hermes binary.
What's under test is the transport (request parsing, response shape, error
mapping), not the invocation contract itself -- that's runtime/hermes.py's
own test suite's job.
"""

from __future__ import annotations

import sys

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
    assert captured["request"].cwd is None


def test_run_passes_through_cwd(monkeypatch, tmp_path):
    captured = {}
    target_cwd = tmp_path / "project-state"

    def fake_run(request, usage_file=None):
        captured["request"] = request
        return HermesResult(response="ok", usage={"failed": False}, exit_code=0, session_id="s")

    monkeypatch.setattr(server_module, "hermes_run", fake_run)

    client.post("/run", json={
        "prompt": "hi", "home_dir": str(tmp_path), "provider": "openrouter",
        "model": "openai/gpt-4o-mini", "cwd": str(target_cwd),
    })

    assert captured["request"].cwd == target_cwd


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


# ---- /exec (Phase 6's toolchain endpoint, RTK-wrapped) -----------------------
#
# argv runs a real subprocess (this test's own Python interpreter) -- no
# mocking here, same convention as tests/test_rtk.py, since what's under
# test is that this endpoint actually launches something and compresses
# its real output, not just that it calls a function correctly.


def test_exec_runs_a_real_command_and_compresses_repetitive_output(tmp_path):
    response = client.post("/exec", json={
        "argv": [sys.executable, "-c", "for i in range(500): print('build: OK')"],
        "cwd": str(tmp_path),
        "tool": "fake-build-tool",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["returncode"] == 0
    assert "[x500]" in body["output"]
    assert body["ratio"] > 0.9


def test_exec_reports_a_real_nonzero_return_code(tmp_path):
    response = client.post("/exec", json={
        "argv": [sys.executable, "-c", "import sys; sys.exit(3)"],
        "cwd": str(tmp_path),
    })

    assert response.status_code == 200
    assert response.json()["returncode"] == 3


def test_exec_rejects_a_cwd_that_does_not_exist(tmp_path):
    response = client.post("/exec", json={
        "argv": [sys.executable, "--version"],
        "cwd": str(tmp_path / "does-not-exist"),
    })

    assert response.status_code == 400


def test_exec_rejects_an_empty_argv(tmp_path):
    response = client.post("/exec", json={"argv": [], "cwd": str(tmp_path)})
    assert response.status_code == 400


def test_exec_maps_a_missing_executable_to_502(tmp_path):
    response = client.post("/exec", json={
        "argv": ["this-command-does-not-exist-anywhere"],
        "cwd": str(tmp_path),
    })
    assert response.status_code == 502


# ---- /index (the container-topology indexing endpoint) ----------------------
#
# DartAnalyzerIndexer itself is monkeypatched here -- no real Dart CLI
# needed to test this endpoint's request/response shape and error mapping.
# The real DartAnalyzerIndexer has its own subprocess-mocked tests
# (tests/test_dart_adapter.py) and was verified live separately.


def test_index_rejects_a_project_with_no_pubspec(tmp_path):
    response = client.post("/index", json={"project_root": str(tmp_path)})
    assert response.status_code == 400
    assert "pubspec.yaml" in response.json()["detail"]


def test_index_returns_the_graph_for_a_real_dart_project(monkeypatch, tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: toy\n", encoding="utf-8")

    from indexing.port import IndexEdge, IndexNode, IndexResult

    def fake_build(self, project_root, **kwargs):
        assert project_root == tmp_path
        return IndexResult(
            nodes=(IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),),
            edges=(IndexEdge(src="lib/a.dart", dst="dart:core", relation="imports"),),
            warnings=("a warning",),
        )

    monkeypatch.setattr(server_module.DartAnalyzerIndexer, "build", fake_build)

    response = client.post("/index", json={"project_root": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == [{"id": "lib/a.dart", "kind": "file", "name": "lib/a.dart", "file": "lib/a.dart", "line": 0, "module": None, "lang": "dart"}]
    assert body["edges"][0]["relation"] == "imports"
    assert body["warnings"] == ["a warning"]


def test_index_maps_a_dart_indexer_failure_to_502(monkeypatch, tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: toy\n", encoding="utf-8")

    from indexing.dart_adapter import DartIndexerError

    def fake_build(self, project_root, **kwargs):
        raise DartIndexerError("dart_indexer exited 1: boom")

    monkeypatch.setattr(server_module.DartAnalyzerIndexer, "build", fake_build)

    response = client.post("/index", json={"project_root": str(tmp_path)})

    assert response.status_code == 502
    assert "boom" in response.json()["detail"]
