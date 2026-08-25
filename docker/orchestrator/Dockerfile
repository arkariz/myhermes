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

# git -- not for a Docker socket or agent-runtime access (this container
# has neither), but because THIS container runs real git subprocesses of
# its own: artifact_versioning.py (artifacts/ as its own repo),
# project_git.py (commit/diff against the project's own source, Phase 5),
# and indexing/freshness.py (current_git_revision(), needed even when
# RemoteIndexer does the actual indexing elsewhere -- freshness itself is
# decided here). Found live: none of the three worked without this.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

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
# indexing/ (not tools/dart_indexer/) -- the Python glue only, no Dart CLI
# or SDK here on purpose. When AGENTIC_RUNTIME_URL is set (this image's
# normal deployment), orchestrator/cli.py and telegram_bot/handlers.py
# both pick indexing/remote.py::RemoteIndexer, which calls agent-runtime's
# POST /index instead of shelling out locally -- DartAnalyzerIndexer is
# still imported here (indexing/dart_adapter.py, unconditionally, by
# orchestrator/cli.py) but never actually invoked in this container.
COPY indexing/ ./indexing/

CMD ["python", "-m", "telegram_bot.bot"]
