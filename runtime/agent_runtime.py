"""AgentRuntime -- the seam between orchestrator/jobs.py (and summarizer.py)
and however Hermes actually gets invoked.

Before this module existed, jobs.py and summarizer.py each independently
did the identical dance: a `try/except ImportError` around
`runtime.hermes`/`runtime.client` imports, then an `if self.runtime_url:`
branch at call time choosing between them. Same four lines of defensive
import, same branch, hand-duplicated in two files -- and a real bug lived
in that duplication once (the summarizer's own copy of `runtime_url`
routing was missing entirely until it was noticed live, in a container
with no Hermes CLI at all).

Two real implementations, selected ONCE, at the entrypoint
(orchestrator/cli.py, telegram_bot/handlers.py) based on whether
AGENTIC_RUNTIME_URL is set -- not re-decided per call. `TurnRunner` and
`summarizer.summarize()` both just take an `AgentRuntime` and call
`.run()`; neither needs to import `runtime.hermes` or `runtime.client`
directly, or know which one is actually behind the call.

The try/except this replaces was defensive against a wheel built from
this project shipping only the `orchestrator` package (the OLD
pyproject.toml hatchling config) -- which would let `orchestrator.jobs`
import successfully while `runtime/` was never on sys.path at all. That
packaging gap is what the layered-package refactor's later phase actually
fixes (one package, `pip install .`); this module doesn't need to keep
guessing around it, so the import here is unconditional.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .client import RuntimeClientError
from .client import run as runtime_client_run
from .hermes import HermesRequest, HermesResult
from .hermes import run as hermes_run

__all__ = [
    "AgentRuntime",
    "InProcessHermesRuntime",
    "HttpAgentRuntime",
    "HermesRequest",
    "HermesResult",
]


class AgentRuntime(Protocol):
    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult: ...


class InProcessHermesRuntime:
    """Calls runtime.hermes.run() directly, in-process -- single-host, no
    container networking. How every project ran before the real
    orchestrator/agent-runtime container split existed."""

    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult:
        return hermes_run(request, usage_file=usage_file)


class HttpAgentRuntime:
    """Routes through runtime/server.py over HTTP -- the real container
    split, where agent-runtime has the Hermes CLI and orchestrator does
    not."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult:
        try:
            return runtime_client_run(request, usage_file=usage_file, base_url=self.base_url)
        except RuntimeClientError as exc:
            raise RuntimeError(f"runtime server call failed: {exc}") from exc
