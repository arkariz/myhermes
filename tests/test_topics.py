"""telegram_bot.topics -- createForumTopic against a mocked httpx transport,
no real network call."""

from __future__ import annotations

import httpx
import pytest

from telegram_bot.topics import ForumTopicError, create_forum_topic


def _client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_create_forum_topic_returns_thread_id(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"ok": True, "result": {
            "message_thread_id": 42, "name": "toy", "icon_color": 0,
        }})

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: _client_with(handler).post(url, json=json))

    thread_id = create_forum_topic("fake-token", 555, "toy")
    assert thread_id == 42
    assert "fake-token" in captured["url"]
    assert b'"chat_id":555' in captured["body"] or b'"chat_id": 555' in captured["body"]


def test_create_forum_topic_raises_on_api_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "ok": False, "description": "Bad Request: chat is not a forum",
        })

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: _client_with(handler).post(url, json=json))

    with pytest.raises(ForumTopicError, match="not a forum"):
        create_forum_topic("fake-token", 555, "toy")


def test_create_forum_topic_raises_on_network_error(monkeypatch):
    def raise_request_error(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", raise_request_error)

    with pytest.raises(ForumTopicError, match="could not reach Telegram"):
        create_forum_topic("fake-token", 555, "toy")
