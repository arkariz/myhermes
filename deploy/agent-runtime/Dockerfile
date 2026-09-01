# agent-runtime -- the only container with the Hermes CLI and the Flutter
# toolchain. Exposes entrypoints/http/server.py's POST /run and POST /exec
# over HTTP; the orchestrator container talks to it, never `docker exec`s
# into it (it has no Docker socket -- least privilege).
#
# Has a real Flutter SDK (stable channel), verified live to run
# `flutter create`/`pub get`/`analyze`/`test`. Deliberately no Android
# SDK/JDK yet -- `flutter build apk` needs both, and installing them is a
# separate, genuinely large download left for its own pass.
#
# Note what's NOT here: no shared Hermes profile volume. HERMES_HOME is set
# per-request from the caller's `home_dir` (adapters/hermes/invocation.py::
# build_env), which always lives under the shared /workspace/agent-state
# bind mount this container mounts -- a project's own
# agent-state/.hermes-home, never a container-wide path. A shared
# /root/.hermes across all requests would reintroduce exactly the
# cross-project memory leak an early spike found and fixed: two projects'
# turns sharing one HERMES_HOME both recalled the same secret.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git jq ripgrep bash xz-utils libatomic1 \
        python3 python3-pip unzip \
    && rm -rf /var/lib/apt/lists/*

# Hermes's own installer pulls its managed Python 3.11, Node.js, ffmpeg.
# That managed Python has no pip and isn't what serves entrypoints/http/
# server.py -- system python3 above does, kept deliberately separate
# (confirmed live: `apt-get install python3-pip` was needed precisely
# because Hermes's own Python has neither pip nor pyyaml).
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
ENV PATH="/root/.local/bin:/root/.hermes/bin:${PATH}"

WORKDIR /app

# `pip install ".[runtime]"` -- fastapi/uvicorn/pydantic only; this image
# never parses YAML workflow config or talks to Telegram, so it never
# pulls pyyaml/tiktoken/python-telegram-bot (the `orchestrator` extra).
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --break-system-packages --no-cache-dir ".[runtime]"

# Flutter SDK (enough for `flutter pub get`, `flutter analyze`,
# `flutter test` against a project's own source -- NOT the Android SDK/JDK
# that `flutter build apk` additionally needs, a genuinely large, separate
# download left for its own pass). A shallow clone of the stable channel
# plus `flutter precache` for the host platform is the same install path
# `flutter doctor` recommends outside Docker.
RUN git clone https://github.com/flutter/flutter.git -b stable --depth 1 /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}"
RUN git config --global --add safe.directory /opt/flutter \
    && flutter precache --no-android --no-ios \
    && flutter config --no-analytics \
    && flutter --version

# tools/dart_indexer/ -- entrypoints/http/server.py's POST /index route
# shells out to the actual Dart CLI here (adapters/indexing/dart.py is
# already installed via `pip install .` above; this is the Dart project
# it invokes). `dart` resolves via PATH to the SDK Flutter's own precache
# just fetched (/opt/flutter/bin/dart), so no separate Dart install is
# needed on top of the Flutter SDK above. `dart pub get` is a one-time
# resolve at build time -- the same step the host-side README instructions
# run manually for local/CLI mode.
COPY tools/dart_indexer/ ./tools/dart_indexer/
RUN cd tools/dart_indexer && dart pub get
ENV DART_INDEXER_DIR=/app/tools/dart_indexer

# Baked in, not left to compose: AGENTIC_WORKSPACE is a fact about this
# image's own filesystem layout (the bind mount always lands at
# /workspace, see compose.yaml), not something that varies per deployment.
ENV AGENTIC_WORKSPACE=/workspace

# Import-time smoke test -- see deploy/orchestrator/Dockerfile's own
# comment for why this exists: turns a missing-module bug from a
# first-request runtime failure into a build failure.
RUN python3 -c "from agentic_dev.entrypoints.http.server import app"

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "agentic_dev.entrypoints.http.server:app", "--host", "0.0.0.0", "--port", "8000"]
