# orchestrator -- state machine, context builder, session manager,
# Telegram bot. No Hermes CLI here at all: every Hermes invocation goes
# through agent-runtime's HTTP API (AGENTIC_RUNTIME_URL), which is the
# whole point of the container split -- this container never touches the
# Docker socket and never launches a subprocess into another container.
#
# config/ is bind-mounted from the host at compose.yaml's discretion, not
# baked into the image -- "edited by hand is fine" (config/projects.yaml's
# own comment) means a config change shouldn't require a rebuild.

FROM python:3.12-slim

WORKDIR /app

# Dependencies only -- not `pip install .`. pyproject.toml's hatchling
# config declares `packages = ["orchestrator"]` (needed for that build
# target elsewhere), which would silently drop telegram_bot/ and runtime/
# from an installed wheel. Running via `python -m` from /app finds all
# three as plain directories on sys.path regardless, so nothing here needs
# to be "installed" as a package -- only their third-party dependencies do.
COPY pyproject.toml ./
RUN pip install --no-cache-dir pyyaml tiktoken fastapi uvicorn httpx python-telegram-bot

COPY orchestrator/ ./orchestrator/
COPY telegram_bot/ ./telegram_bot/
COPY runtime/__init__.py runtime/hermes.py runtime/client.py ./runtime/

CMD ["python", "-m", "telegram_bot.bot"]
