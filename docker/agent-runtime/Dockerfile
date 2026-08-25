# agent-runtime -- the only container with the Hermes CLI and the Flutter
# toolchain. Exposes runtime/server.py's POST /run and POST /exec over
# HTTP; the orchestrator container talks to it, never `docker exec`s into
# it (it has no Docker socket -- least privilege).
#
# This is docker/spike/Dockerfile's base, promoted from a throwaway spike
# image to the real service: same Hermes install, now serving runtime/
# instead of a bash shell.
#
# Has a real Flutter SDK (stable channel), verified live to run
# `flutter create`/`pub get`/`analyze`/`test`. Deliberately no Android
# SDK/JDK yet -- `flutter build apk` needs both, and installing them is a
# separate, genuinely large download left for its own pass.
#
# Note what's NOT here: no shared Hermes profile volume. HERMES_HOME is set
# per-request from the caller's `home_dir` (runtime/hermes.py::build_env),
# which always lives under the shared agent-state volume this container
# mounts -- a project's own agent-state/.hermes-home, never a container-wide
# path. A shared /root/.hermes across all requests would reintroduce
# exactly the cross-project memory leak the Phase 1 spike found and fixed
# (docs/plan.md "Phase 1 spike"): two projects' turns sharing one
# HERMES_HOME both recalled the same secret.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git jq ripgrep bash xz-utils libatomic1 \
        python3 python3-pip unzip \
    && rm -rf /var/lib/apt/lists/*

# Hermes's own installer pulls its managed Python 3.11, Node.js, ffmpeg.
# That managed Python has no pip and isn't what serves runtime/server.py --
# system python3 above does, kept deliberately separate (confirmed live,
# Phase 1: `apt-get install python3-pip` was needed precisely because
# Hermes's own Python has neither pip nor pyyaml).
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
ENV PATH="/root/.local/bin:/root/.hermes/bin:${PATH}"

WORKDIR /app
COPY runtime/hermes.py runtime/server.py runtime/rtk.py runtime/__init__.py ./runtime/
RUN pip install --break-system-packages --no-cache-dir fastapi uvicorn httpx

# Flutter SDK (Phase 6, first slice): enough for `flutter pub get`,
# `flutter analyze`, `flutter test` against a project's own source --
# NOT the Android SDK/JDK that `flutter build apk` additionally needs
# (a genuinely large, separate download, left for its own pass). A shallow
# clone of the stable channel plus `flutter precache` for the host platform
# is the same install path `flutter doctor` recommends outside Docker.
RUN git clone https://github.com/flutter/flutter.git -b stable --depth 1 /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}"
RUN git config --global --add safe.directory /opt/flutter \
    && flutter precache --no-android --no-ios \
    && flutter config --no-analytics \
    && flutter --version

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "runtime.server:app", "--host", "0.0.0.0", "--port", "8000"]
