"""HTTP client for runtime/server.py -- the same `run()` contract as
runtime.hermes.run(), reachable over HTTP instead of a direct function call.

This is the orchestrator side of the container-topology boundary: when
agent-runtime is a separate container, `orchestrator/jobs.py` calls this
instead of importing `runtime.hermes.run()` in-process. Kept as its own
module (not folded into hermes.py) because it has a genuinely different
failure mode -- a network/HTTP problem reaching the server is not the same
thing as Hermes itself failing a turn, and callers need to tell the two
apart.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from .invocation import HermesRequest, HermesResult


class RuntimeClientError(Exception):
    """The runtime server could not be reached, or returned something that
    isn't a normal turn outcome (network failure, 5xx, malformed body).
    Distinct from HermesResult.failed, which is a turn that ran and failed
    -- this is the call itself not completing."""


def run(
    request: HermesRequest,
    *,
    usage_file: Path | None = None,
    base_url: str,
    timeout_seconds: float = 600.0,
) -> HermesResult:
    """Same contract as runtime.hermes.run(), over HTTP.

    `base_url` is the runtime server's address (e.g.
    "http://agent-runtime:8000" inside compose, or "http://localhost:8000"
    for a local server) -- the caller's job to know, not this function's;
    it has no default because a silently-wrong default is worse than a
    required argument.
    """
    payload = {
        "prompt": request.prompt,
        "home_dir": str(request.home_dir),
        "provider": request.provider,
        "model": request.model,
        "toolsets": request.toolsets,
        "skills": request.skills,
        "resume_session_id": request.resume_session_id,
        "usage_file": str(usage_file) if usage_file else None,
        "cwd": str(request.cwd) if request.cwd else None,
    }

    try:
        response = httpx.post(f"{base_url}/run", json=payload, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise RuntimeClientError(f"could not reach runtime server at {base_url}: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeClientError(
            f"runtime server returned {response.status_code}: {response.text}"
        )

    body = response.json()
    return HermesResult(
        response=body["response"],
        usage=body["usage"],
        exit_code=body["exit_code"],
        session_id=body["session_id"],
    )
