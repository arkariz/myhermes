"""Bootstraps a fresh workspace repo from the shipped config template.

This is the one tested path from a clean clone of the code repo to
`agentic turn ...` actually working: `settings.py`'s own resolution order
expects a sibling directory named `agentic-workspace` (or an explicit
AGENTIC_WORKSPACE) holding `config/workflow.yaml`. Bootstrapping by hand
-- copying files, remembering the `.gitignore` rules for `agent-state/`
and `projects/` -- is exactly the kind of setup step that's easy to get
subtly wrong once and never notice.

The workspace is a SEPARATE git repo from the code repo (ADR-0001): it
tracks `config/` (roles, budgets, workflow, personas) and ignores
`agent-state/` (generated indexes and turn history) and `projects/` (your
actual project repos, each with their own `.git` -- nesting them inside
this repo would be its own bug).
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
from pathlib import Path

_GITIGNORE = """\
# Generated state -- never tracked here. Each project under projects/ is
# its own git repo; agent-state/ is regenerated indexes and turn history.
/agent-state/
/projects/
"""


class WorkspaceAlreadyExists(Exception):
    """Refusing to overwrite an existing workspace's config/."""


def init_workspace(target: Path) -> Path:
    target = target.resolve()
    if (target / "config").exists():
        raise WorkspaceAlreadyExists(f"{target / 'config'} already exists -- refusing to overwrite")

    template = importlib.resources.files("agentic_dev") / "templates" / "workspace" / "config"
    shutil.copytree(str(template), str(target / "config"))
    (target / "agent-state").mkdir(parents=True, exist_ok=True)
    (target / "projects").mkdir(parents=True, exist_ok=True)
    (target / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

    if not (target / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        # A per-repo identity, not the user's global one -- see
        # adapters/git/artifacts.py's ensure_repo for the same idiom.
        subprocess.run(
            ["git", "config", "user.email", "agent@agentic-dev.local"], cwd=target, check=True,
        )
        subprocess.run(["git", "config", "user.name", "Agentic Dev"], cwd=target, check=True)
        subprocess.run(["git", "add", "config", ".gitignore"], cwd=target, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "Initial workspace config"], cwd=target, check=True,
        )

    return target
