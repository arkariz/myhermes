"""indexing.dart_adapter -- exercised with subprocess.run mocked (same
discipline as runtime.hermes's own tests: no real dart_indexer invocation
here, no dependency on the Dart SDK being installed to run the suite).
The real Dart CLI has its own test suite (tools/dart_indexer/test/) and
was verified live separately.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from indexing.dart_adapter import DartAnalyzerIndexer, DartIndexerError
from indexing.port import IndexEdge, IndexNode


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_supports_a_project_with_a_pubspec(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: toy\n", encoding="utf-8")
    assert DartAnalyzerIndexer().supports(tmp_path) is True


def test_does_not_support_a_project_without_a_pubspec(tmp_path):
    assert DartAnalyzerIndexer().supports(tmp_path) is False


def test_build_normalizes_nodes_and_edges(monkeypatch, tmp_path):
    graph_json = json.dumps({
        "nodes": [
            {"id": "lib/app.dart#MyApp", "kind": "widget", "name": "MyApp",
             "file": "lib/app.dart", "line": 3, "module": None, "lang": "dart"},
        ],
        "edges": [
            {"src": "lib/app.dart", "dst": "lib/app.dart#MyApp", "relation": "declares", "confidence": 1.0},
        ],
    })
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=graph_json))

    result = DartAnalyzerIndexer().build(tmp_path)

    assert result.nodes == (IndexNode(
        id="lib/app.dart#MyApp", kind="widget", name="MyApp",
        file="lib/app.dart", line=3, module=None, lang="dart",
    ),)
    assert result.edges == (IndexEdge(
        src="lib/app.dart", dst="lib/app.dart#MyApp", relation="declares", confidence=1.0,
    ),)


def test_build_captures_warnings_from_stderr(monkeypatch, tmp_path):
    graph_json = json.dumps({"nodes": [], "edges": []})
    stderr = "warning: could not parse lib/broken.dart: syntax error\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=graph_json, stderr=stderr))

    result = DartAnalyzerIndexer().build(tmp_path)

    assert result.warnings == ("could not parse lib/broken.dart: syntax error",)


def test_build_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _completed(returncode=2, stderr="Usage: ..."),
    )
    with pytest.raises(DartIndexerError, match="exited 2"):
        DartAnalyzerIndexer().build(tmp_path)


def test_build_raises_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout="not json"))
    with pytest.raises(DartIndexerError, match="invalid JSON"):
        DartAnalyzerIndexer().build(tmp_path)


def test_build_raises_when_dart_cannot_be_launched(monkeypatch, tmp_path):
    def raise_os_error(*a, **k):
        raise OSError("dart not found")

    monkeypatch.setattr(subprocess, "run", raise_os_error)
    with pytest.raises(DartIndexerError, match="failed to launch"):
        DartAnalyzerIndexer().build(tmp_path)


def test_dart_executable_defaults_to_env_var(monkeypatch):
    monkeypatch.setenv("DART_EXECUTABLE", "/custom/path/to/dart")
    assert DartAnalyzerIndexer().dart_executable == "/custom/path/to/dart"


def test_dart_executable_falls_back_to_plain_dart(monkeypatch):
    monkeypatch.delenv("DART_EXECUTABLE", raising=False)
    assert DartAnalyzerIndexer().dart_executable == "dart"
