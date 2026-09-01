"""Create a Telegram forum topic for a project -- one topic, one project.

Plain synchronous HTTP against the Bot API, not python-telegram-bot's async
`Bot` class: this is called from `orchestrator/cli.py project new`, a
synchronous command that has no event loop anywhere else in it, and one
REST call doesn't justify giving it one.

Requires the target chat to already be a supergroup with Topics enabled,
and the bot to be an admin there with "Manage Topics" permission --
Telegram's own requirement for `createForumTopic`, not something worth
working around here. A chat that doesn't meet this returns a normal
`{"ok": false, "description": ...}` body, surfaced as `ForumTopicError`.
"""

from __future__ import annotations

import httpx


class ForumTopicError(Exception):
    """createForumTopic failed -- e.g. the chat isn't a Topics-enabled
    supergroup, or the bot isn't an admin with manage-topics rights there."""


def create_forum_topic(token: str, chat_id: int, name: str, *, timeout: float = 10.0) -> int:
    """Create a forum topic named `name` in `chat_id`.

    Returns the new topic's thread id -- the same value routing.py and
    ProjectRegistry.link_telegram() key on for every later message and
    approval in that topic.
    """
    url = f"https://api.telegram.org/bot{token}/createForumTopic"
    try:
        response = httpx.post(url, json={"chat_id": chat_id, "name": name}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ForumTopicError(f"could not reach Telegram: {exc}") from exc

    data = response.json()
    if not data.get("ok"):
        raise ForumTopicError(
            f"createForumTopic failed: {data.get('description', 'unknown error')}"
        )
    return data["result"]["message_thread_id"]
