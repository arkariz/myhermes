"""Artifact versioning via git (a Phase 3 gap docs/progress.md names
explicitly): `artifacts/` becomes a real git repo, so "how did the PRD
evolve" is `git log` / `git diff`, not a bare integer counter with no
record of what actually changed between revisions.

Scoped deliberately to just `artifacts/`, not the whole agent-state tree:
`turns/`, `conversations/`, and `events.jsonl` are already append-only or
immutable, so they have their own history for free. `artifacts/` is the one
thing overwritten in place, and therefore the one thing that actually needs
git's diffing to answer "what changed and when."

This module doesn't care how an artifact's content got there -- an agent's
own file-write tool, a future explicit "extract artifact from response"
step, or a human editing it by hand all look the same here: whatever is on
disk after a turn gets snapshotted if it differs from the last snapshot.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_repo(artifacts_dir: Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if (artifacts_dir / ".git").exists():
        return
    subprocess.run(["git", "init", "-q"], cwd=artifacts_dir, check=True)
    # A per-repo identity, not the user's global one -- these commits are
    # machine-authored snapshots, not human commits, and should read as such.
    subprocess.run(
        ["git", "config", "user.email", "agent@agentic-dev.local"],
        cwd=artifacts_dir, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agentic Dev"], cwd=artifacts_dir, check=True,
    )


def commit(artifacts_dir: Path, message: str) -> str | None:
    """Snapshot the current state of artifacts_dir if it changed since the
    last snapshot. Returns the new commit hash, or None if there was
    nothing to commit -- an empty commit would be noise, not history.
    """
    ensure_repo(artifacts_dir)
    subprocess.run(["git", "add", "-A"], cwd=artifacts_dir, check=True)

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=artifacts_dir,
        capture_output=True, text=True, check=True,
    )
    if not status.stdout.strip():
        return None

    subprocess.run(["git", "commit", "-q", "-m", message], cwd=artifacts_dir, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=artifacts_dir,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def log(artifacts_dir: Path, artifact_name: str | None = None, limit: int = 20) -> list[dict]:
    """Commit history, optionally scoped to one artifact file. Empty list
    for a project with no commits yet, not an error -- a brand new project
    asking about history isn't a mistake, and neither is a repo that exists
    (ensure_repo runs even when commit() finds nothing to commit) but has
    never had a successful commit."""
    if not (artifacts_dir / ".git").exists():
        return []

    argv = ["git", "log", f"-{limit}", "--format=%H%x1f%aI%x1f%s"]
    if artifact_name:
        argv += ["--", artifact_name]
    result = subprocess.run(argv, cwd=artifacts_dir, capture_output=True, text=True)
    if result.returncode != 0:
        return []  # e.g. "does not have any commits yet"

    entries = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        commit_hash, date, subject = line.split("\x1f")
        entries.append({"commit": commit_hash, "date": date, "message": subject})
    return entries


def diff(artifacts_dir: Path, artifact_name: str, commit_a: str, commit_b: str = "HEAD") -> str:
    """The literal text diff of one artifact between two commits -- what a
    reviewer or a human approver would actually want to read, not just
    "revision 4 vs revision 5"."""
    result = subprocess.run(
        ["git", "diff", commit_a, commit_b, "--", artifact_name],
        cwd=artifacts_dir, capture_output=True, text=True, check=True,
    )
    return result.stdout
