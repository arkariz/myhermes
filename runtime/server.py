"""FastAPI service wrapping runtime/hermes.py -- the HTTP boundary between
the orchestrator container and the agent-runtime container.

Why this exists (brief's container topology): the orchestrator has no
Docker socket (least privilege), so it cannot `docker exec` into
agent-runtime to run Hermes there. agent-runtime instead exposes this small
internal HTTP API, and the orchestrator calls it like any other service.

    uvicorn runtime.server:app --host 0.0.0.0 --port 8000

Both containers mount the same `/workspace/agent-state` volume (rw), so a
`home_dir` or `usage_file` path in a request is valid on both sides without
needing to ship file contents over the wire -- only the path crosses the
HTTP boundary, the same way it already crosses process boundaries when
`orchestrator/jobs.py` calls `runtime.hermes.run()` in-process today.

Not yet wired into orchestrator/jobs.py -- TurnRunner still calls
runtime.hermes.run() as a direct in-process function call, which is how
Phase 1/2 were verified live without needing Docker networking at all.
Switching jobs.py to call this over HTTP (via an httpx client) instead is
the next step toward the real container topology, not done here: that
change touches every existing hermes_run-monkeypatch test, so it deserves
its own pass rather than riding along with this one.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from runtime.hermes import HermesInvocationError, HermesRequest
from runtime.hermes import run as hermes_run

app = FastAPI(title="agentic-dev runtime")


class RunRequest(BaseModel):
    prompt: str
    home_dir: str
    provider: str
    model: str
    toolsets: str | None = None
    skills: str | None = None
    resume_session_id: str | None = None
    usage_file: str | None = None  # ignored when resume_session_id is set


class RunResponse(BaseModel):
    response: str
    usage: dict
    exit_code: int
    session_id: str | None
    failed: bool


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
def run(payload: RunRequest) -> RunResponse:
    """Execute one Hermes turn -- the same contract as runtime.hermes.run(),
    over HTTP instead of a function call. Argv selection (`-z` vs.
    `chat -q --resume`), env (HERMES_HOME), and response cleaning are all
    unchanged; this endpoint is a thin transport, not a second
    implementation of the invocation contract.
    """
    request = HermesRequest(
        prompt=payload.prompt,
        home_dir=Path(payload.home_dir),
        provider=payload.provider,
        model=payload.model,
        toolsets=payload.toolsets,
        skills=payload.skills,
        resume_session_id=payload.resume_session_id,
    )
    usage_file = Path(payload.usage_file) if payload.usage_file else None

    try:
        result = hermes_run(request, usage_file=usage_file)
    except HermesInvocationError as exc:
        # The Hermes binary itself couldn't be started -- a transport-level
        # failure, not a turn that ran and failed. 502 (bad gateway) fits:
        # this service couldn't reach the thing it wraps.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RunResponse(
        response=result.response,
        usage=result.usage,
        exit_code=result.exit_code,
        session_id=result.session_id,
        failed=result.failed,
    )
