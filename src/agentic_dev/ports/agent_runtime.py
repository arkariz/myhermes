"""AgentRuntime -- the seam between app/turn_runner.py (and app/summarizer.py)
and however Hermes actually gets invoked.

Before this port existed, jobs.py and summarizer.py each independently did
the identical dance: a `try/except ImportError` around `runtime.hermes`/
`runtime.client` imports, then an `if self.runtime_url:` branch at call
time choosing between them. Same four lines of defensive import, same
branch, hand-duplicated in two files -- and a real bug lived in that
duplication once (the summarizer's own copy of `runtime_url` routing was
missing entirely until it was noticed live, in a container with no Hermes
CLI at all).

Two real implementations -- `InProcessHermesRuntime` and `HttpAgentRuntime`
-- live in `adapters/hermes/runtime.py`, not here: a port declares the
contract (the `AgentRuntime` Protocol, and the request/result shape both
sides agree on), it does not import the concrete adapters that satisfy it.
Only `app/` and `entrypoints/` need to see the concrete classes; this
module is what `domain` and `ports` themselves are allowed to depend on.

`HermesRequest`/`HermesResult` live here (not in `adapters/hermes/
invocation.py`, where the actual subprocess plumbing lives) because they
ARE the port's contract -- what `AgentRuntime.run()` takes and returns,
shared by both the in-process and HTTP implementations. `adapters/hermes/
invocation.py` and `adapters/hermes/http_client.py` both import them from
here, not the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = ["AgentRuntime", "HermesRequest", "HermesResult"]


@dataclass(frozen=True)
class HermesRequest:
    prompt: str
    home_dir: Path            # HERMES_HOME -- the verified isolation boundary
    provider: str
    model: str
    toolsets: str | None = None      # comma-separated
    skills: str | None = None        # comma-separated
    resume_session_id: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None   # None -- each adapter's own default
    cwd: Path | None = None   # where a `file`-toolset write/read call
                               # resolves ITS OWN relative paths against --
                               # unset means "whatever this process's own
                               # cwd happens to be" (e.g. /app in either
                               # container image), which is never a
                               # project's real directory. The context
                               # prompt tells the agent about paths like
                               # "artifacts/prd.md" or "lib/app.dart" --
                               # this is what makes those the SAME relative
                               # path the agent's own tools resolve, rather
                               # than two different, unconnected notions of
                               # "here." Found live: a role with the
                               # `file` toolset granted still had nowhere
                               # correct to write, because nothing set this.

    @property
    def is_continuation(self) -> bool:
        return self.resume_session_id is not None


@dataclass
class HermesResult:
    response: str
    usage: dict[str, Any]
    exit_code: int
    session_id: str | None

    @property
    def failed(self) -> bool:
        return bool(self.usage.get("failed")) or self.exit_code not in (0, 2)


class AgentRuntime(Protocol):
    def run(self, request: HermesRequest, *, usage_file: Path | None = None) -> HermesResult: ...
