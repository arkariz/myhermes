"""Hermes CLI invocation -- the verified contract, not the assumed one.

Corrections from the Phase 1 spike (docs/plan.md "Phase 1 spike"), each
load-bearing and each verified by reading source or running a real call,
not assumed from documentation:

  * HERMES_HOME, not HERMES_PROFILE, is the isolation boundary. Every
    invocation for a project MUST set HERMES_HOME to that project's own
    directory (ProjectStore.hermes_home()) or memory bleeds across projects.

  * --usage-file and --resume are NOT both available on one invocation path:
      - `-z` (hermes_cli/oneshot.py::run_oneshot) accepts --usage-file but
        its signature has no `resume` parameter at all, and none of its 3
        call sites in main.py pass one -- `-z --resume` is silently dead
        code even though argparse accepts the flag.
      - `chat -q --resume <id>` genuinely resumes (confirmed live: recalled
        a fact from 3 turns back), but `chat`'s own subparser has no
        --usage-file option at all (confirmed: passing it is a hard
        argparse error, not a silent no-op).
    This maps cleanly onto the state machine's own rule -- only
    collaborative states resume, autonomous/gate turns always rebuild -- so
    the two invocation paths below are chosen by turn kind, not layered.

  * Post-hoc usage for a resumed turn comes from
    `hermes sessions export - --format jsonl --session-id <id>`, which
    carries the same input_tokens/output_tokens/cache_read_tokens/
    estimated_cost_usd fields as --usage-file (confirmed live).

  * --ignore-rules / --safe-mode do not stop the background memory writer
    that causes the HERMES_HOME leak above, so they buy nothing further once
    HERMES_HOME is doing the isolating. Dropped from the invocation --
    --safe-mode additionally ignores config.yaml's model.default, which is
    why provider/model are always passed explicitly instead.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class HermesInvocationError(Exception):
    """The hermes binary could not be started at all (not a turn failure)."""


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


def build_argv(request: HermesRequest, *, usage_file: Path | None) -> list[str]:
    """The exact argv for one turn.

    A continuation turn (resume_session_id set) always goes through
    `chat -q --resume`; usage_file is ignored for it -- the caller recovers
    usage via build_usage_export_argv() afterward instead. A full turn
    always goes through top-level `-z`, the only path that honors
    --usage-file.
    """
    if request.is_continuation:
        argv = [
            "hermes", "chat",
            "-q", request.prompt,
            "--quiet",
            "--resume", request.resume_session_id,
            "--provider", request.provider,
            "--model", request.model,
            # Hermes remembers the working directory a session was FIRST
            # opened in and, by default, cd's back into it on every resume
            # -- silently overriding whatever `cwd` this call actually
            # started the subprocess in. Found live: a resumed session
            # opened before this module set `cwd` at all kept restoring
            # `/app` (the container's bare WORKDIR) on every later turn,
            # so a role's file writes landed in a directory that gets
            # thrown away the moment the container restarts, even though
            # the subprocess's own cwd was by then set correctly. Our own
            # `cwd` (computed fresh, every turn, from the project's real
            # state) must always win over Hermes's stale historical guess.
            "--no-restore-cwd",
        ]
    else:
        argv = [
            "hermes", "-z", request.prompt,
            "--provider", request.provider,
            "--model", request.model,
        ]
        if usage_file is not None:
            argv += ["--usage-file", str(usage_file)]

    if request.toolsets:
        argv += ["--toolsets", request.toolsets]
        # Without this, a role with e.g. `file`/`terminal` granted hits
        # Hermes's own interactive tool-call approval prompt -- which has
        # no TTY to answer it in a non-interactive `-z`/`chat -q`
        # subprocess, so the call just hangs until the HTTP client's own
        # timeout gives up. Found live: a real turn that should have taken
        # seconds instead ran until a 600s ReadTimeout. Our own role-based
        # toolset grants in config/agents.yaml ARE the approval mechanism
        # here -- a human decides once, at config time, which tools a role
        # may use at all, not per call at turn time -- so bypassing
        # Hermes's own per-call prompt is the correct behavior for this
        # architecture, not a safety hole opened casually.
        argv += ["--yolo"]
    if request.skills:
        argv += ["--skills", request.skills]
    return argv


def build_usage_export_argv(session_id: str) -> list[str]:
    """Recover usage for a continuation turn -- chat -q has no --usage-file."""
    return [
        "hermes", "sessions", "export", "-", "--format", "jsonl",
        "--session-id", session_id,
    ]


def build_env(request: HermesRequest) -> dict[str, str]:
    """Environment for the subprocess.

    HERMES_HOME is set unconditionally -- this is the one thing that must
    never be left to a default, per the spike finding.
    """
    env = dict(os.environ)
    env["HERMES_HOME"] = str(request.home_dir)
    env.update(request.extra_env)
    return env


def parse_usage_file(path: Path) -> dict[str, Any]:
    """Read a --usage-file report (full turns only).

    Written even on failure (per Hermes's own docs), so this is safe to call
    unconditionally after the subprocess returns.
    """
    if not path.exists():
        return {"failed": True, "failure": "usage file was not written"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"failed": True, "failure": f"unreadable usage file: {exc}"}


def parse_session_export(jsonl_text: str) -> dict[str, Any]:
    """Parse `sessions export` JSONL output into a usage-shaped dict.

    The export is a superset of --usage-file's fields (it's the whole
    session record); pull out just the accounting fields so callers can
    treat both turn kinds the same way downstream.
    """
    line = jsonl_text.strip().splitlines()[-1] if jsonl_text.strip() else ""
    if not line:
        return {"failed": True, "failure": "session export was empty"}
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"failed": True, "failure": f"unparseable session export: {exc}"}

    fields = (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd",
        "actual_cost_usd", "cost_status", "cost_source", "model", "provider",
    )
    usage = {k: record.get(k) for k in fields}
    usage["session_id"] = record.get("id")
    usage["failed"] = False
    return usage


def extract_session_id(stdout: str, usage: dict[str, Any]) -> str | None:
    """Recover a session id when it isn't already known (full turns only).

    Prefer the usage report's session_id; fall back to a "session_id: <id>"
    line if one appears on stdout. Continuation turns never need this --
    the id being resumed is already known input, not something to extract.
    """
    sid = usage.get("session_id")
    if sid:
        return str(sid)
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("session_id:"):
            return line.split(":", 1)[1].strip()
    return None


# Status-icon prefixes Hermes's CLI uses for banners/warnings that can land
# on stdout ahead of (or interleaved with, on `chat -q`) the real response,
# confirmed live rather than assumed -- e.g. a security-scanner notice with
# no blank-line separator before the actual answer. `-z`'s stdout is clean
# by design ("nothing else on stdout" per its own docs) and needs none of
# this; only `chat -q --resume` output does.
_NOISE_PREFIXES = ("⚠", "↻", "⚡", "→", "✓")


def _clean_chat_response(stdout: str) -> str:
    """Strip known CLI banner/status lines from a `chat -q` response.

    There is no reliable structural marker separating banner from response
    on this path (confirmed live: "session_id:" goes to stderr here, not
    stdout, unlike what -z's docs describe) -- filtering by known status-icon
    prefixes is a pragmatic heuristic given the CLI's actual output shape,
    not a guarantee. Document any prefix this misses if one turns up.
    """
    lines = [
        line for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith(_NOISE_PREFIXES)
    ]
    return "\n".join(lines).strip()


def run(
    request: HermesRequest,
    *,
    usage_file: Path | None = None,
    timeout_seconds: int = 600,
    runner=subprocess.run,
) -> HermesResult:
    """Execute one turn -- full via `-z`, continuation via `chat -q --resume`.

    `runner` is injectable so unit tests exercise argv/env/parsing without a
    real Hermes binary or network call.
    """
    request.home_dir.mkdir(parents=True, exist_ok=True)
    if usage_file is not None:
        usage_file.parent.mkdir(parents=True, exist_ok=True)
    if request.cwd is not None:
        request.cwd.mkdir(parents=True, exist_ok=True)

    argv = build_argv(request, usage_file=usage_file)
    env = build_env(request)

    try:
        proc = runner(
            argv, env=env, cwd=request.cwd, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HermesInvocationError(f"failed to launch hermes: {exc}") from exc

    stdout = proc.stdout or ""

    if request.is_continuation:
        session_id = request.resume_session_id
        export_argv = build_usage_export_argv(session_id)
        try:
            export_proc = runner(
                export_argv, env=env, capture_output=True, text=True,
                timeout=timeout_seconds,
            )
            usage = parse_session_export(export_proc.stdout or "")
        except (OSError, subprocess.SubprocessError) as exc:
            usage = {"failed": True, "failure": f"usage export failed: {exc}"}
    else:
        usage = parse_usage_file(usage_file) if usage_file else {}
        session_id = extract_session_id(stdout, usage)

    # -z's stdout is the raw response, nothing else, by the CLI's own design.
    # chat -q's stdout can carry banner noise with no structural separator,
    # confirmed live -- only that path needs cleaning.
    response = _clean_chat_response(stdout) if request.is_continuation else stdout.strip()

    return HermesResult(
        response=response,
        usage=usage,
        exit_code=proc.returncode,
        session_id=session_id,
    )
