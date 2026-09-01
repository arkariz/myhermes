"""Single source of truth for where config/agent-state/projects live, and
for every env-derived setting this codebase reads. Nothing else here
should evaluate `Path(__file__)` to find its way around -- under a
packaged install that expression resolves into site-packages, not the
real workspace, and every consumer of a wrong path degrades silently
rather than raising (a missing soul, a missing index, a missing project
source all just quietly do nothing -- see docs/progress.md's own history
of exactly that class of bug). Five modules independently did this before
this module existed, each with a different `parents[N]`, and any one of
them breaking on a file move would have failed silently.

Resolution order, first hit wins:
  1. $AGENTIC_WORKSPACE          -- explicit; what both containers set
  2. `../agentic-workspace`, sibling of the code repo root -- local dev.
     The code repo root itself is found by searching upward from CWD for
     tools/dart_indexer/pubspec.yaml (a marker in the CODE, not the
     workspace); the sibling directory then has to actually hold
     config/workflow.yaml, or this tier doesn't count as a hit either.
  3. `workspace` stays `None`

Resolution itself never raises -- `Settings.from_env()` (and the
module-level `settings` singleton it produces at import time) must always
succeed, or nothing importable anywhere in this codebase could even be
imported before a workspace exists, `agentic init-workspace` included.
Instead, every workspace-DERIVED property (`config_dir`, `souls_dir`,
`workflow_file`, ...) raises `WorkspaceNotFound` the moment it's actually
accessed with no workspace resolved -- still loud, just deferred to first
real use instead of import time. Given the silent-failure history above,
guessing a default remains worse than stopping with a clear message; it's
only the WHEN that moved.

The workspace is a SEPARATE git repo from this one (ADR-0001) -- it tracks
config/ (roles, budgets, workflow, personas) and ignores agent-state/ and
projects/ (your actual project repos live there, each with their own
.git). `agentic init-workspace <path>` bootstraps a fresh one from the
template shipped inside this package at templates/workspace/config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_WORKSPACE_MARKER = Path("config") / "workflow.yaml"
_REPO_MARKER = Path("tools") / "dart_indexer" / "pubspec.yaml"
_SIBLING_WORKSPACE_NAME = "agentic-workspace"


class WorkspaceNotFound(Exception):
    """No workspace could be located. Not a bug -- a setup step."""

    def __init__(self, cwd: Path | None = None):
        cwd = cwd or Path.cwd()
        super().__init__(
            f"No workspace found from {cwd} (looked for a sibling "
            f"'{_SIBLING_WORKSPACE_NAME}' directory holding {_WORKSPACE_MARKER}). "
            f"Set AGENTIC_WORKSPACE, or run `agentic init-workspace "
            f"../{_SIBLING_WORKSPACE_NAME}` to create one."
        )


def _search_up(start: Path, marker: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / marker).is_file():
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class Settings:
    workspace: Path | None     # holds config/ (and, later, agent-state/, projects/); None until resolved
    dart_indexer_dir: Path     # tools/dart_indexer -- SHIPPED code, different lifetime
    runtime_url: str | None
    telegram_bot_token: str | None
    telegram_forum_chat_id: int | None
    telegram_default_host_root: Path | None

    @property
    def config_dir(self) -> Path:
        if self.workspace is None:
            raise WorkspaceNotFound()
        return self.workspace / "config"

    @property
    def souls_dir(self) -> Path:
        return self.config_dir / "souls"

    @property
    def workflow_file(self) -> Path:
        return self.config_dir / "workflow.yaml"

    @property
    def agents_file(self) -> Path:
        return self.config_dir / "agents.yaml"

    @property
    def models_file(self) -> Path:
        return self.config_dir / "models.yaml"

    @property
    def projects_file(self) -> Path:
        return self.config_dir / "projects.yaml"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> "Settings":
        env = os.environ if env is None else env
        cwd = Path.cwd() if cwd is None else Path(cwd)

        repo = _search_up(cwd.resolve(), _REPO_MARKER)

        ws: Path | None
        if explicit := env.get("AGENTIC_WORKSPACE"):
            ws = Path(explicit).resolve()
        else:
            sibling = (repo.parent / _SIBLING_WORKSPACE_NAME) if repo else None
            ws = sibling if sibling and (sibling / _WORKSPACE_MARKER).is_file() else None

        chat_id = (env.get("TELEGRAM_FORUM_CHAT_ID") or "").strip()

        if explicit_host_root := env.get("TELEGRAM_DEFAULT_HOST_ROOT"):
            host_root: Path | None = Path(explicit_host_root)
        else:
            host_root = (ws / "projects") if ws is not None else None

        return cls(
            workspace=ws,
            dart_indexer_dir=Path(
                env.get("DART_INDEXER_DIR")
                or str((repo or cwd) / "tools" / "dart_indexer")
            ),
            runtime_url=env.get("AGENTIC_RUNTIME_URL") or None,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_forum_chat_id=int(chat_id) if chat_id else None,
            telegram_default_host_root=host_root,
        )


settings = Settings.from_env()  # module-level default, evaluated at import time


def reload(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Settings:
    """Recompute and replace the module-level default -- the test/bootstrap
    seam. Prefer threading a `Settings` instance explicitly where possible
    (e.g. `build_application(token, *, config_dir=...)` already does);
    this exists for the few places that still reach for the module global.
    """
    global settings
    settings = Settings.from_env(env=env, cwd=cwd)
    return settings
