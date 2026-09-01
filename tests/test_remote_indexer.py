"""indexing.remote.RemoteIndexer -- exercised with httpx mocked (same
discipline as runtime.client's own tests: no real network call here). The
receiving end, runtime/server.py's POST /index, has its own tests in
tests/test_server.py.
"""

from __future__ import annotations

import httpx
import pytest

from agentic_dev.adapters.indexing.dart import DartIndexerError
from agentic_dev.adapters.indexing.remote import RemoteIndexer


class _FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        return self._json_body


def test_supports_is_a_local_check_not_a_network_call(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("supports() should never make a network call")

    monkeypatch.setattr(httpx, "post", fail_if_called)

    indexer = RemoteIndexer(base_url="http://agent-runtime:8000")
    assert indexer.supports(tmp_path) is False

    (tmp_path / "pubspec.yaml").write_text("name: toy\n", encoding="utf-8")
    assert indexer.supports(tmp_path) is True


def test_build_posts_the_project_root_and_parses_the_graph(tmp_path, monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, json_body={
            "nodes": [{"id": "lib/a.dart", "kind": "file", "name": "lib/a.dart", "file": "lib/a.dart", "line": 0}],
            "edges": [{"src": "lib/a.dart", "dst": "dart:core", "relation": "imports"}],
            "warnings": [],
        })

    monkeypatch.setattr(httpx, "post", fake_post)

    indexer = RemoteIndexer(base_url="http://agent-runtime:8000")
    result = indexer.build(tmp_path)

    assert captured["url"] == "http://agent-runtime:8000/index"
    assert captured["json"] == {"project_root": str(tmp_path)}
    assert len(result.nodes) == 1
    assert result.nodes[0].id == "lib/a.dart"
    assert len(result.edges) == 1


def test_build_raises_on_a_server_error_response(monkeypatch, tmp_path):
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: _FakeResponse(502, text="dart_indexer exited 1"))

    with pytest.raises(DartIndexerError, match="502"):
        RemoteIndexer(base_url="http://agent-runtime:8000").build(tmp_path)


def test_build_raises_when_the_server_is_unreachable(monkeypatch, tmp_path):
    def raise_connect_error(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", raise_connect_error)

    with pytest.raises(DartIndexerError, match="could not reach"):
        RemoteIndexer(base_url="http://agent-runtime:8000").build(tmp_path)
