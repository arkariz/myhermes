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
  2. nearest ancestor of CWD holding config/workflow.yaml -- local dev,
     works from any subdirectory of the repo (src/, tests/, the root)
  3. raise WorkspaceNotFound, naming what to actually do about it

Tier 3 fails LOUDLY on purpose. Given the silent-failure history above,
guessing a default is worse than stopping with a clear message.

(A later phase of the layered-package refactor, ADR-0001, moves the
marker to workspace/config/workflow.yaml once config/ becomes its own
sibling repo -- this module is the only place that change touches.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_WORKSPACE_MARKER = Path("config") / "workflow.yaml"
_REPO_MARKER = Path("tools") / "dart_indexer" / "pubspec.yaml"


class WorkspaceNotFound(Exception):
    """No workspace could be located. Not a bug -- a setup step."""

    def __init__(self, cwd: Path):
        super().__init__(
            f"No workspace found from {cwd} upward (looked for {_WORKSPACE_MARKER}). "
            f"Set AGENTIC_WORKSPACE, or run from inside the repo."
        )


def _search_up(start: Path, marker: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / marker).is_file():
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class Settings:
    workspace: Path            # holds config/ (and, later, agent-state/, projects/)
    dart_indexer_dir: Path     # tools/dart_indexer -- SHIPPED code, different lifetime
    runtime_url: str | None
    telegram_bot_token: str | None
    telegram_forum_chat_id: int | None
    telegram_default_host_root: Path

    @property
    def config_dir(self) -> Path:
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

        if explicit := env.get("AGENTIC_WORKSPACE"):
            ws = Path(explicit).resolve()
        else:
            found = _search_up(cwd.resolve(), _WORKSPACE_MARKER)
            if found is None:
                raise WorkspaceNotFound(cwd)
            ws = found

        repo = _search_up(cwd.resolve(), _REPO_MARKER)
        chat_id = (env.get("TELEGRAM_FORUM_CHAT_ID") or "").strip()

        return cls(
            workspace=ws,
            dart_indexer_dir=Path(
                env.get("DART_INDEXER_DIR")
                or str((repo or cwd) / "tools" / "dart_indexer")
            ),
            runtime_url=env.get("AGENTIC_RUNTIME_URL") or None,
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_forum_chat_id=int(chat_id) if chat_id else None,
            telegram_default_host_root=Path(
                env.get("TELEGRAM_DEFAULT_HOST_ROOT") or str(ws / "projects")
            ),
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
