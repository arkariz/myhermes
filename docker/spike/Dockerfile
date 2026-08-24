# Minimal, fast-iterating image for the Phase 1 Hermes retention spike.
# NOT the final agent-runtime image (that adds Flutter/Android/JDK/RTK later,
# per docs/plan.md Step 2) -- this exists only to answer the four spike
# questions as quickly as possible.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git jq ripgrep bash xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Hermes's own installer pulls Python 3.11, Node.js, ffmpeg, git as needed.
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

ENV PATH="/root/.local/bin:/root/.hermes/bin:${PATH}"
WORKDIR /spike

CMD ["bash"]
