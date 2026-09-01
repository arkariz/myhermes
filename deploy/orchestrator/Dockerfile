# orchestrator -- state machine, context builder, session manager,
# Telegram bot. No Hermes CLI here at all: every Hermes invocation goes
# through agent-runtime's HTTP API (AGENTIC_RUNTIME_URL), which is the
# whole point of the container split -- this container never touches the
# Docker socket and never launches a subprocess into another container.
#
# config/ lives inside the /workspace bind mount (compose.yaml), not baked
# into the image -- "edited by hand is fine" (the shipped
# config/projects.yaml template's own comment) means a config change
# shouldn't require a rebuild.

FROM python:3.12-slim

# git -- not for a Docker socket or agent-runtime access (this container
# has neither), but because THIS container runs real git subprocesses of
# its own: adapters/git/artifacts.py (artifacts/ as its own repo),
# adapters/git/project.py (commit/diff against the project's own source),
# and adapters/indexing/freshness.py (current_git_revision(), needed even
# when RemoteIndexer does the actual indexing elsewhere -- freshness itself
# is decided here). Found live: none of the three worked without this.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# `pip install ".[orchestrator]"` -- the extras split (pyproject.toml)
# means this image never pulls in fastapi/uvicorn/pydantic, which
# agent-runtime alone needs. Copying pyproject.toml + src/ (not the whole
# repo) is what makes this a real, buildable package install rather than
# the hand-duplicated `pip install pyyaml tiktoken ...` line this replaced
# -- which is exactly the selective-COPY bug class that once shipped
# runtime/rtk.py missing from this image entirely.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[orchestrator]"

# Baked in, not left to compose: AGENTIC_WORKSPACE is a fact about this
# image's own filesystem layout (the bind mount always lands at
# /workspace, see compose.yaml), not something that varies per deployment.
ENV AGENTIC_WORKSPACE=/workspace

# Import-time smoke test: turns "a module this image needs failed to
# import" from a first-request runtime failure into a build failure. This
# is the exact class of bug the old selective COPY once shipped silently
# (runtime/rtk.py omitted when /exec landed -- the route would fail to
# import, discovered live, not at build time).
RUN python -c "import agentic_dev.entrypoints.telegram.bot"

CMD ["agentic-bot"]
