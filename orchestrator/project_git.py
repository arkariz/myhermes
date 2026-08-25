"""Real git integration against a project's own source tree -- not
agent-state's git repos (see `artifact_versioning.py`, which owns a
*separate* repo under `artifacts/`; this module operates on the actual
Flutter/Dart project a human owns).

Powers Phase 5's builder -> review -> qa loop: the builder's changes need
to land somewhere reviewer/qa can read a real diff from, instead of the
denylist-blinded roles getting no diff-aware context at all (the named gap
in docs/progress.md Phase 5).

Deliberately never creates a git repo where one doesn't exist. Unlike
`artifacts/` (an internal bookkeeping tree this project owns outright and
`ensure_repo()` may freely `git init`), a project's source tree is the
user's own -- `current_revision()`/`commit_all()`/`diff()` all degrade to
"nothing to report" for a non-git project, the same convention
`indexing/freshness.py::current_git_revision()` already established,
rather than silently `git init`-ing a directory nobody asked us to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The canonical git empty-tree object hash (Git itself computes this the
# same way in every repository) -- used as the diff base when there is no
# real "before" commit yet, so the first builder commit still diffs as
# "everything in it is new" rather than crashing on a missing revision.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class ProjectGitError(Exception):
    """A git operation failed unexpectedly on a project tree already
    confirmed to be a git repo. Not raised for "this isn't a git repo" --
    that is a normal, supported case, not an error."""


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def is_git_repo(project_root: Path) -> bool:
    result = _run(["git", "rev-parse", "--is-inside-work-tree"], project_root)
    return result.returncode == 0 and result.stdout.strip() == "true"


def current_revision(project_root: Path) -> str | None:
    """The project's current commit, or None if it isn't a git repo, or is
    one with no commits yet."""
    result = _run(["git", "rev-parse", "HEAD"], project_root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def commit_all(project_root: Path, message: str) -> str | None:
    """Stage and commit every change in the project's working tree.

    Returns the new commit hash, or None if there was nothing to commit --
    an empty commit would be noise, not history (same reasoning as
    `artifact_versioning.commit()`). Uses a per-repo machine identity so
    these commits read as agent-authored, never masquerading as the
    project's human contributors.

    Raises `ProjectGitError` if `project_root` isn't a git repo -- callers
    are expected to check `is_git_repo()` first, the same
    capability-then-act pattern `DartAnalyzerIndexer.supports()` already
    uses.
    """
    if not is_git_repo(project_root):
        raise ProjectGitError(f"{project_root} is not a git repository")

    _run(["git", "add", "-A"], project_root)
    status = _run(["git", "status", "--porcelain"], project_root)
    if not status.stdout.strip():
        return None

    result = _run(
        [
            "git",
            "-c", "user.email=agent@agentic-dev.local",
            "-c", "user.name=Agentic Dev",
            "commit", "-q", "-m", message,
        ],
        project_root,
    )
    if result.returncode != 0:
        raise ProjectGitError(f"git commit failed: {result.stderr.strip()}")
    return current_revision(project_root)


def diff(project_root: Path, base_revision: str | None, *, to_revision: str = "HEAD") -> str:
    """The literal unified diff between two revisions -- what a
    diff-aware reviewer/QA context actually needs, not a summary of it.

    Empty string whenever there's nothing meaningful to show: not a git
    repo, no commits at either end, or a `base_revision` that no longer
    resolves. `base_revision=None` (a project with zero commits when the
    builder's base was captured) diffs against git's empty-tree object, so
    a first-ever commit still shows as "everything in it is new" instead
    of erroring.
    """
    if not is_git_repo(project_root):
        return ""
    base = base_revision or _EMPTY_TREE
    result = _run(["git", "diff", f"{base}..{to_revision}"], project_root)
    if result.returncode != 0:
        return ""
    return result.stdout
