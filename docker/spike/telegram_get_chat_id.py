"""Print the chat_id/title/type of recent chats the bot has seen -- the
piece you need to set TELEGRAM_FORUM_CHAT_ID.

Usage:
    1. Make sure no other process is long-polling this bot right now
       (Telegram allows only one getUpdates poller per bot at a time --
       run telegram_live_test.py or telegram_bot.bot first and this will
       just 409 against it). Kill it before running this.
    2. Send any message in the target group (the one you enabled Topics on
       and added the bot to as admin).
    3. python docker/spike/telegram_get_chat_id.py

Reads the token from secrets/.env.telegram (gitignored). Never logs or
prints the token or the request URL -- only the parsed chat fields, which
is the whole point of this script existing instead of just cURL-ing
getUpdates and reading the raw response yourself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    _load_env_file(REPO_ROOT / "secrets" / ".env.telegram")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set (expected it via secrets/.env.telegram)"
        )

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = httpx.get(url, params={"limit": 50}, timeout=10.0)
    except httpx.HTTPError as exc:
        raise SystemExit(f"could not reach Telegram: {exc}") from exc

    data = response.json()
    if not data.get("ok"):
        raise SystemExit(f"getUpdates failed: {data.get('description', 'unknown error')}")

    results = data.get("result", [])
    if not results:
        print(
            "No recent updates. Send a message in the target group first "
            "(and make sure no other process is already polling this bot)."
        )
        return

    seen: dict[int, dict] = {}
    for update in results:
        message = update.get("message") or update.get("channel_post")
        if not message:
            continue
        chat = message.get("chat", {})
        seen[chat["id"]] = chat

    if not seen:
        print("Updates arrived, but none were plain messages with chat info.")
        return

    print(f"{'chat_id':<20} {'type':<12} title")
    for chat_id, chat in seen.items():
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "(no title)"
        print(f"{chat_id:<20} {chat.get('type', '?'):<12} {title}")

    print()
    print("Use the chat_id of the Topics-enabled supergroup as TELEGRAM_FORUM_CHAT_ID.")


if __name__ == "__main__":
    main()
