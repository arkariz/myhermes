"""The two real `AgentRuntime` implementations (see `ports/agent_runtime.py`
for the Protocol and the `HermesRequest`/`HermesResult` contract types both
of these share).

Selected ONCE, at the entrypoint (entrypoints/cli.py,
entrypoints/telegram/handlers.py) based on whether AGENTIC_RUNTIME_URL is
set -- not re-decided per call. `TurnRunner` and `summarizer.summarize()`
both just take an `AgentRuntime` and call `.run()`; neither needs to know
which one is actually behind the call.

`hermes_run`/`runtime_client_run` are bound at module level (not called via
`invocation.run`/`http_client.run` directly inside each method) specifically
so tests can monkeypatch `adapters.hermes.runtime.hermes_run` -- Python
resolves a function's globals at CALL time, not at import time, so
patching the name on this module is what actually takes effect inside
`InProcessHermesRuntime.run()` below.
"""

from __future__ import annotations

from pathlib import Path

from ...ports.agent_runtime import HermesRequest, HermesResult
from .http_client import RuntimeClientError
from .http_client import run as runtime_client_run
from .invocation import run as hermes_run

__all__ = ["InProcessHermesRuntime", "HttpAgentRuntime"]


class InProcessHermesRuntime:
    """Calls adapters.hermes.invocation.run() directly, in-process --
    single-host, no container networking. How every project ran before the
    real orchestrator/agent-runtime container split existed."""

    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult:
        return hermes_run(request, usage_file=usage_file)


class HttpAgentRuntime:
    """Routes through entrypoints/http/server.py over HTTP -- the real
    container split, where agent-runtime has the Hermes CLI and
    orchestrator does not."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult:
        try:
            return runtime_client_run(request, usage_file=usage_file, base_url=self.base_url)
        except RuntimeClientError as exc:
            raise RuntimeError(f"runtime server call failed: {exc}") from exc
