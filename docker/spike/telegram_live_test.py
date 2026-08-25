"""Live smoke test for telegram_bot -- real Telegram Bot API, mocked Hermes,
against the REAL project registry (config/projects.yaml).

What this proves that the unit tests (fakes for Update/context) can't: the
bot actually connects to Telegram's real long-polling endpoint, a real human
in a real chat/topic can get a real response with a real inline button, and
clicking that button really advances the project's state.yaml on disk.

What this deliberately does NOT test: Hermes/OpenRouter. That path was
already verified live in the Phase 1 spike (docs/plan.md). hermes_run is
monkeypatched here with canned responses so this costs zero API spend --
the only new surface in Phase 2 is the Telegram wiring, so that's the only
thing this needs to exercise for real.

Uses the real config/projects.yaml on purpose (not a scratch registry):
`orchestrator.cli project new` (with TELEGRAM_BOT_TOKEN/TELEGRAM_FORUM_
CHAT_ID set) is what creates and auto-links a project's forum topic, and
this script needs to see that same registry to route messages from it.
Remember to `git checkout -- config/projects.yaml` after testing, the same
way you'd clean up any other scratch state this generates.

Usage:
    python docker/spike/telegram_live_test.py

Reads the bot token from secrets/.env.telegram (gitignored, never printed).
"""

from __future__ import annotations

import itertools
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


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

    import orchestrator.jobs as jobs_module
    from runtime.hermes import HermesResult
    from telegram_bot import bot as bot_module

    config_dir = REPO_ROOT / "config"

    canned = itertools.cycle([
        "Got it -- before I draft the discovery doc: is this meant to stay "
        "single-user long-term, or is that just v1 scope?",
        "Noted. Draft scope: users can create habits, mark them done for "
        "today, and see a per-habit streak counter. Let me know if that "
        "matches what you had in mind, or type more detail and I'll revise.",
    ])

    def fake_hermes_run(request, usage_file=None):
        return HermesResult(
            response=next(canned), usage={"failed": False, "session_id": "live-test-session"},
            exit_code=0, session_id="live-test-session",
        )

    jobs_module.hermes_run = fake_hermes_run

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx (python-telegram-bot's HTTP client) logs the full request URL at
    # INFO -- and every Bot API URL embeds the token
    # (api.telegram.org/bot<TOKEN>/getMe). Silence it; WARNING+ never
    # includes the URL. Found live: the first run of this script leaked the
    # token into its own output this way -- do not lower this again.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print("Bot starting against the REAL config/projects.yaml registry.")
    print("Message a project's linked chat/topic directly, or /link <project_id>")
    print("in a topic that isn't linked yet.")
    print()

    app = bot_module.build_application(token, config_dir=config_dir)
    app.run_polling()


if __name__ == "__main__":
    main()
