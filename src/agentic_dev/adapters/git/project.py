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

import re
import subprocess
from pathlib import Path

# The canonical git empty-tree object hash (Git itself computes this the
# same way in every repository) -- used as the diff base when there is no
# real "before" commit yet, so the first builder commit still diffs as
# "everything in it is new" rather than crashing on a missing revision.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Deliberately narrow -- HTTPS github.com only, no other host, no ssh
# form. This is the one URL string in the whole system that reaches a
# subprocess argv straight from a Telegram message a human typed, so the
# allowlist itself is the security boundary, not just validation: `--`
# before the URL in clone() stops an argv that starts with `-` from being
# read as a git flag, and this regex stops anything that isn't obviously
# a real repository URL from reaching that argv at all. Broaden later
# (other hosts, ssh) only with the same care, not by loosening this.
GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class ProjectGitError(Exception):
    """A git operation failed unexpectedly on a project tree already
    confirmed to be a git repo. Not raised for "this isn't a git repo" --
    that is a normal, supported case, not an error."""


class InvalidRepositoryUrl(Exception):
    """A clone() URL that isn't a recognizable https://github.com/<owner>/
    <repo> URL -- rejected before it ever reaches a subprocess argv."""


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # A directory that doesn't exist yet is a real, expected case here --
    # e.g. a project just /create'd from Telegram, before any source has
    # been dropped into its host_path -- not a bug. subprocess.run doesn't
    # degrade gracefully on its own (it raises FileNotFoundError /
    # NotADirectoryError before ever producing a CompletedProcess), so
    # every caller in this module would otherwise crash the turn instead
    # of getting the "not a git repo" answer they already know how to
    # handle. Found live: exactly this crash, driving through the
    # ordinary /create -> implementation path with no source yet.
    if not cwd.is_dir():
        return subprocess.CompletedProcess(args=argv, returncode=128, stdout="", stderr="not a directory")
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


def repo_name_from_url(url: str) -> str:
    """The `<repo>` in `https://github.com/<owner>/<repo>` -- the natural
    default project name for something imported this way. Raises
    InvalidRepositoryUrl for anything clone() would also reject, so a
    caller can validate+derive-the-name in one call before doing anything
    that touches the filesystem."""
    match = GITHUB_HTTPS_RE.match(url.strip())
    if not match:
        raise InvalidRepositoryUrl(f"not a github.com HTTPS URL: {url!r}")
    return match.group(2)


def clone(url: str, dest: Path) -> None:
    """Clone a GitHub repository into `dest`, which must not already exist.

    Shallow (--depth 50): onboarding needs recent history for context
    (`git log`), not the complete archive -- a full clone of a large
    repository would be a slow, unnecessary cost for a one-time audit.
    `--` before the URL is a second, independent safeguard alongside
    GITHUB_HTTPS_RE (see its own comment) against an argv that starts with
    `-` being read as a git flag instead of a positional URL.
    """
    if not GITHUB_HTTPS_RE.match(url.strip()):
        raise InvalidRepositoryUrl(f"not a github.com HTTPS URL: {url!r}")
    if dest.exists():
        raise ProjectGitError(f"{dest} already exists -- refusing to clone over it")

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "50", "--", url.strip(), str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProjectGitError(f"git clone failed: {result.stderr.strip()}")
