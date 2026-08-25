"""Telegram entry point -- MVP: same process as the orchestrator (brief S24
permits this; a separate bot process is a Phase 3+ concern, not now).

    python -m telegram_bot.bot

Requires TELEGRAM_BOT_TOKEN in the environment. Hermes never sees Telegram
(brief S24) -- this bot is ours, long-polling via python-telegram-bot,
driving the exact same TurnRunner that orchestrator/cli.py drives.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from orchestrator.registry import ProjectRegistry
from settings import settings

from . import handlers

logger = logging.getLogger(__name__)

# Single source of truth for both CommandHandler registration (below) and
# the autocomplete menu Telegram shows when a user types "/" (via
# setMyCommands in _post_init) -- one list can't drift from the other.
COMMANDS = [
    ("create", "Create a new project and its Telegram topic", handlers.cmd_create),
    ("link", "Link this chat/topic to an existing project", handlers.cmd_link),
    ("status", "Show a project's current workflow state", handlers.cmd_status),
]


async def _post_init(app: Application) -> None:
    """post_init hook: runs once, after the bot's own event loop is up but
    before polling starts. Two things need exactly that timing:

      * setMyCommands is itself an API call -- it needs a running loop, so
        it can't happen at build_application() time.
      * the inbox worker must not start consuming jobs before the bot is
        actually ready to send their responses back.
    """
    await app.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc, _ in COMMANDS])
    app.bot_data["worker_task"] = asyncio.create_task(handlers.inbox_worker(app))


def build_application(token: str, *, config_dir: Path | None = None) -> Application:
    config_dir = config_dir if config_dir is not None else settings.config_dir
    app = Application.builder().token(token).post_init(_post_init).build()

    app.bot_data["config_dir"] = config_dir
    app.bot_data["souls_dir"] = config_dir / "souls"
    app.bot_data["registry"] = ProjectRegistry(config_dir / "projects.yaml")
    app.bot_data["inbox"] = asyncio.Queue()
    app.bot_data["callback_table"] = {}  # token -> handlers.CallbackPayload
    app.bot_data["pending_create"] = {}  # (chat_id, thread_id) -> awaiting a /create name

    for cmd, _desc, handler_fn in COMMANDS:
        app.add_handler(CommandHandler(cmd, handler_fn))
    app.add_handler(CallbackQueryHandler(handlers.handle_approve_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))

    return app


def main() -> None:
    token = settings.telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    logging.basicConfig(level=logging.INFO)
    # httpx logs the full request URL at INFO, and every Bot API URL embeds
    # the token (api.telegram.org/bot<TOKEN>/...) -- confirmed live, the
    # hard way. WARNING+ never includes the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = build_application(token)
    app.run_polling()


if __name__ == "__main__":
    main()
