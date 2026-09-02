"""FastAPI service wrapping runtime/hermes.py -- the HTTP boundary between
the orchestrator container and the agent-runtime container.

Why this exists (brief's container topology): the orchestrator has no
Docker socket (least privilege), so it cannot `docker exec` into
agent-runtime to run Hermes there. agent-runtime instead exposes this small
internal HTTP API, and the orchestrator calls it like any other service.

    uvicorn runtime.server:app --host 0.0.0.0 --port 8000

Both containers mount the same `/workspace/agent-state` and
`/workspace/projects` volumes (rw), so a `home_dir`, `usage_file`, or
`project_root` path in a request is valid on both sides without needing to
ship file contents over the wire -- only the path crosses the HTTP
boundary, the same way it already crosses process boundaries when
`orchestrator/jobs.py` calls `runtime.hermes.run()` in-process today.

`orchestrator/jobs.py`'s `TurnRunner` calls this over HTTP via
`runtime/client.py` when `AGENTIC_RUNTIME_URL` is set -- unset (the
default), it still calls `runtime.hermes.run()` in-process, which is how
Phase 1/2 were verified live without needing Docker networking at all.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...adapters.indexing.dart import DartAnalyzerIndexer, DartIndexerError
from ...adapters.exec import rtk
from ...adapters.hermes.invocation import HermesInvocationError
from ...adapters.hermes.invocation import run as hermes_run
from ...ports.agent_runtime import HermesRequest

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
    cwd: str | None = None  # see HermesRequest.cwd -- where the agent's own
                             # file/terminal tool calls resolve relative paths
    timeout_seconds: int | None = None


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
        cwd=Path(payload.cwd) if payload.cwd else None,
        timeout_seconds=payload.timeout_seconds,
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


class ExecRequest(BaseModel):
    argv: list[str]
    cwd: str
    tool: str | None = None
    timeout_seconds: float = 300.0


class ExecResponse(BaseModel):
    returncode: int
    output: str
    original_bytes: int
    compressed_bytes: int
    ratio: float


@app.post("/exec", response_model=ExecResponse)
def exec_command(payload: ExecRequest) -> ExecResponse:
    """Run a real toolchain command (`flutter pub get`/`analyze`/`test`/
    `build`, `git`, `rg`, ...) and return its RTK-compressed output.

    `argv` is a literal list, executed without a shell -- there is no
    string concatenation for a shell to reinterpret, so this is not a
    shell-injection surface the way a single command string would be.
    This is still a "run what I'm told" endpoint by design (the brief's
    `POST /exec`): it trusts its caller, exactly as `/run` trusts its
    caller to have already built a safe prompt. That caller is
    agent-runtime's *own* orchestrator container, reachable only over the
    internal compose network -- nothing external ever calls this directly.
    """
    cwd = Path(payload.cwd)
    if not cwd.is_dir():
        raise HTTPException(status_code=400, detail=f"cwd does not exist: {cwd}")
    if not payload.argv:
        raise HTTPException(status_code=400, detail="argv must not be empty")

    try:
        result = rtk.run(
            payload.argv, tool=payload.tool, cwd=cwd, timeout_seconds=payload.timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=502, detail=f"could not launch {payload.argv[0]!r}: {exc}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    return ExecResponse(
        returncode=result.returncode,
        output=result.compression.text,
        original_bytes=result.compression.original_bytes,
        compressed_bytes=result.compression.compressed_bytes,
        ratio=result.compression.ratio,
    )


class IndexRequest(BaseModel):
    project_root: str


class IndexResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    warnings: list[str]


@app.post("/index", response_model=IndexResponse)
def index(payload: IndexRequest) -> IndexResponse:
    """Build a codebase index for `project_root` -- the endpoint the plan's
    original container-topology diagram names (`POST /index`), for the
    same reason `/run` and `/exec` exist: the Dart SDK lives only in
    agent-runtime, deliberately not duplicated into the orchestrator
    image, so indexing has to happen here when the two are separate
    containers. `indexing/remote.py::RemoteIndexer` is the client side --
    same `CodebaseIndexer` shape as `DartAnalyzerIndexer`, so
    `TurnRunner`/`IndexProvider` don't know or care which one they're
    holding.
    """
    project_root = Path(payload.project_root)
    indexer = DartAnalyzerIndexer()
    if not indexer.supports(project_root):
        raise HTTPException(
            status_code=400, detail=f"{project_root} is not a Dart/Flutter project (no pubspec.yaml)",
        )

    try:
        result = indexer.build(project_root)
    except DartIndexerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexResponse(
        nodes=[asdict(n) for n in result.nodes],
        edges=[asdict(e) for e in result.edges],
        warnings=list(result.warnings),
    )
