"""Freshness/incremental indexing by git commit (docs/plan.md Phase 4).

An index is a snapshot of a project's source at some commit. Rebuilding it
every turn would be wasteful, and worse: `index_revision` is one of the
eight session-boundary triggers (`SessionManager._boundary_reason`), so a
value that changes on every turn regardless of whether the source actually
changed would invalidate every session pointlessly. This module decides
whether a rebuild is actually needed and persists the result plus its
metadata so later turns can reuse it without re-running the indexer.

Never rebuilt per turn -- the gap the brief calls out Pang leaving open.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .port import CodebaseIndexer, IndexEdge, IndexNode, IndexResult


@dataclass(frozen=True)
class IndexMetadata:
    tool: str
    source_revision: str | None
    generated_at: str


def current_git_revision(project_root: Path) -> str | None:
    """The project's current commit, or None if it isn't a git repo (or
    has no commits yet, or doesn't exist yet at all -- e.g. a project just
    /create'd from Telegram, before any source has been dropped into its
    host_path). Freshness degrades to "always rebuild" rather than
    raising -- all three are real, supportable cases, not errors.

    A missing directory needs an explicit guard: subprocess.run raises
    FileNotFoundError/NotADirectoryError before producing a
    CompletedProcess for a nonexistent cwd, rather than a normal nonzero
    exit code -- found live via exactly that crash.
    """
    if not project_root.is_dir():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_root,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _metadata_path(index_dir: Path) -> Path:
    return index_dir / "metadata.json"


def _graph_path(index_dir: Path) -> Path:
    return index_dir / "graph.json"


def load_metadata(index_dir: Path) -> IndexMetadata | None:
    path = _metadata_path(index_dir)
    if not path.exists():
        return None
    return IndexMetadata(**json.loads(path.read_text(encoding="utf-8")))


def save_metadata(index_dir: Path, metadata: IndexMetadata) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    _metadata_path(index_dir).write_text(
        json.dumps(asdict(metadata), indent=2), encoding="utf-8",
    )


def save_graph(index_dir: Path, result: IndexResult) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": [asdict(n) for n in result.nodes],
        "edges": [asdict(e) for e in result.edges],
    }
    _graph_path(index_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_graph(index_dir: Path) -> IndexResult | None:
    path = _graph_path(index_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = tuple(IndexNode(**n) for n in data["nodes"])
    edges = tuple(IndexEdge(**e) for e in data["edges"])
    return IndexResult(nodes=nodes, edges=edges)


def ensure_fresh(
    indexer: CodebaseIndexer, project_root: Path, index_dir: Path,
) -> tuple[IndexResult, str | None]:
    """Return (graph, source_revision) for `project_root`'s current commit,
    rebuilding only if it differs from what's cached (or nothing is cached
    yet, or the project isn't under git -- source_revision is then None
    and a rebuild happens every call, since there's no cheaper freshness
    signal available).
    """
    current_rev = current_git_revision(project_root)
    cached_meta = load_metadata(index_dir)

    if (
        current_rev is not None
        and cached_meta is not None
        and cached_meta.source_revision == current_rev
    ):
        cached_graph = load_graph(index_dir)
        if cached_graph is not None:
            return cached_graph, current_rev

    result = indexer.build(project_root)
    save_graph(index_dir, result)
    save_metadata(index_dir, IndexMetadata(
        tool=type(indexer).__name__, source_revision=current_rev,
        generated_at=datetime.now(timezone.utc).isoformat(),
    ))
    return result, current_rev
