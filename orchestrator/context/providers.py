"""Concrete context providers -- Phase 1 scope only.

Per docs/plan.md Step 4: "role soul, project identity, current artifact,
relevant decisions, last three turns, explicitly referenced files." Nothing
more elaborate belongs here yet -- Phase 4 (codebase intelligence) adds
retrieval by adding a new provider, not by editing ContextBuilder.

Each provider reads from disk lazily inside `collect()`, not at
construction, so building a ContextBuilder is cheap and I/O only happens
when a turn actually runs.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from ..store import ProjectStore
from .budget import ContextItem
from .builder import BuildRequest


class RoleSoulProvider:
    """The role's persona/instructions -- souls/<role>.md.

    Layer 1 / volatility 0: this is the most stable part of the prompt (it
    only changes when the soul file itself is edited), so it anchors the
    cacheable prefix.
    """

    name = "role_soul"

    def __init__(self, souls_dir: Path | str = "souls"):
        self.souls_dir = Path(souls_dir)

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        path = self.souls_dir / f"{request.role}.md"
        if not path.exists():
            return []
        return [ContextItem(
            key=f"souls/{request.role}.md",
            layer=1, priority=1, volatility=0,
            reason="role soul", content=path.read_text(encoding="utf-8"),
        )]


class ProjectIdentityProvider:
    """Stable project facts -- name, vision, platform, conventions.

    Layer 1 / volatility 0: alongside the soul, this is the other half of
    the cacheable prefix. Sourced from agent-state/context.md if present;
    a project with none yet still gets a minimal identity line from
    state.yaml so the prompt is never missing this layer entirely.
    """

    name = "project_identity"

    def __init__(self, store: ProjectStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        context_file = self.store.root / "context.md"
        if context_file.exists():
            content = context_file.read_text(encoding="utf-8")
        else:
            content = f"# Project: {self.project_id}\n\n(no context.md yet)"
        return [ContextItem(
            key="context.md", layer=1, priority=1, volatility=0,
            reason="project identity", content=content,
        )]


class ArtifactSectionProvider:
    """The current state's working artifact -- e.g. artifacts/prd.md.

    Layer 2 / volatility 3: changes across turns as the agent revises it,
    so it sits after the stable prefix but before the fastest-moving layers.
    """

    name = "artifact"

    def __init__(self, store: ProjectStore):
        self.store = store

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        if not request.artifact_name:
            return []
        path = self.store.artifact(request.artifact_name)
        if not path.exists():
            return []
        return [ContextItem(
            key=f"artifacts/{request.artifact_name}",
            layer=2, priority=4, volatility=3,
            reason="current working artifact",
            content=path.read_text(encoding="utf-8"),
        )]


class DecisionsProvider:
    """Persisted decisions -- decisions/*.md.

    Layer 2 / priority 3: per S16, decisions are "what the project currently
    believes" and take priority over conversation history when the budget is
    tight (brief S17.2 ranks decisions above recent turns).
    """

    name = "decisions"

    def __init__(self, store: ProjectStore):
        self.store = store

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        d = self.store.decisions_dir()
        if not d.exists():
            return []
        items = []
        for path in sorted(d.glob("*.md")):
            items.append(ContextItem(
                key=f"decisions/{path.name}", layer=2, priority=3, volatility=2,
                reason="recorded decision", content=path.read_text(encoding="utf-8"),
            ))
        return items


class SummaryProvider:
    """The running summary for this workflow state -- summarizer.py's own
    output (brief S17.3).

    Layer 4, alongside recent turns: the brief's own cache-layout diagram
    groups "recent summary, recent turns, current human request" together.
    Placed at a slightly lower volatility than raw recent turns (5 vs 6)
    since a summary is rewritten incrementally each turn rather than being
    wholesale-replaced -- in practice closer to stable than a brand new
    message is.
    """

    name = "summary"

    def __init__(self, store: ProjectStore):
        self.store = store

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        content = self.store.read_summary(request.workflow_state)
        if not content:
            return []
        return [ContextItem(
            key=f"summaries/{request.workflow_state}.md",
            layer=4, priority=6, volatility=5,
            reason="running summary carried across session boundaries",
            content=content,
        )]


class RecentTurnsProvider:
    """The last N raw conversation turns for the current workflow state.

    Layer 4 / volatility 6, the most volatile layer -- different on every
    turn by construction, so it always renders last regardless of budget
    pressure elsewhere.
    """

    name = "recent_turns"

    def __init__(self, store: ProjectStore, count: int = 3):
        self.store = store
        self.count = count

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        d = self.store.conversation_dir(request.workflow_state)
        if not d.exists():
            return []
        turns = sorted(d.glob("*.md"))[-self.count:]
        items = []
        for path in turns:
            items.append(ContextItem(
                key=f"conversations/{request.workflow_state}/{path.name}",
                layer=4, priority=7, volatility=6,
                reason=f"one of the last {self.count} turns",
                content=path.read_text(encoding="utf-8"),
            ))
        return items


class ReferencedFilesProvider:
    """Explicitly named source paths -- guided-retrieval roles only.

    These are handed over as references (content=None), not embedded: the
    agent reads them itself with its own tools, capped by request.read_budget.
    """

    name = "referenced_files"

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        return [
            ContextItem(
                key=path, layer=3, priority=5, volatility=4,
                reason="explicitly referenced for this task", content=None,
            )
            for path in request.referenced_paths
        ]


_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def _pubspec_package_name(project_source_root: Path) -> str | None:
    pubspec = project_source_root / "pubspec.yaml"
    if not pubspec.exists():
        return None
    for line in pubspec.read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def _resolve_import(src_file: str, uri: str, package_name: str | None) -> str | None:
    """Map an import's raw URI to a project-relative file path, or None if
    it doesn't resolve to one (an SDK import, or a different package).

    The Dart indexer stores import edges by raw URI (`dst: uri`), not a
    resolved file -- that resolution needs no type analysis, just path
    math, so it's done here rather than promoted into "resolved analysis"
    (which needs `flutter pub get` and a real `AnalysisContextCollection`
    for `calls`/`instantiates` -- still deferred, see docs/progress.md).
    """
    if uri.startswith("dart:"):
        return None
    if uri.startswith("package:"):
        if package_name is None:
            return None
        prefix = f"package:{package_name}/"
        if not uri.startswith(prefix):
            return None  # a dependency's own package, not this project's graph
        return f"lib/{uri[len(prefix):]}"
    # A relative import resolves against the importing file's own directory.
    base_dir = posixpath.dirname(src_file)
    return posixpath.normpath(posixpath.join(base_dir, uri))


class IndexProvider:
    """The codebase index's file inventory (docs/plan.md Phase 4:
    "symbol+file retrieval ... as new context providers feeding
    guided-retrieval paths") -- guided-retrieval roles get a real list of
    what exists in the project instead of nothing.

    Handed over as references (content=None), same as ReferencedFiles --
    an inventory is where to look, not what to read; the agent reads with
    its own tools, capped by request.read_budget.

    Three priority tiers, lower selected first when the budget is tight:
      5  ReferencedFilesProvider  -- explicit "read this file for this task"
      7  dependency expansion     -- imported by an explicitly referenced file
      8  relevance match          -- a task keyword appears in the file's path
      9  plain inventory          -- present in the index, nothing more known

    Dependency expansion follows only the *referenced* files' own imports,
    one hop -- not a transitive closure over the whole graph, which would
    make "expanded" indistinguishable from "everything." Relevance ranking
    is a plain keyword-in-path match, not embeddings or an LLM call: cheap,
    deterministic (same task text always ranks the same way, which
    reproducibility depends on), and good enough to break ties in a large
    inventory without adding a dependency this project doesn't otherwise need.
    """

    name = "index"

    def __init__(self, store: ProjectStore, project_source_root: Path | str | None = None):
        self.store = store
        self.project_source_root = Path(project_source_root) if project_source_root else None

    def _expand_dependencies(self, graph, referenced_paths: tuple[str, ...], file_set: set[str]) -> list[str]:
        package_name = (
            _pubspec_package_name(self.project_source_root)
            if self.project_source_root is not None else None
        )
        referenced_set = set(referenced_paths)
        expanded: list[str] = []
        seen: set[str] = set()
        for edge in graph.edges:
            if edge.relation != "imports" or edge.src not in referenced_set:
                continue
            resolved = _resolve_import(edge.src, edge.dst, package_name)
            if resolved and resolved in file_set and resolved not in seen:
                seen.add(resolved)
                expanded.append(resolved)
        return expanded

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        from indexing.freshness import load_graph  # local: only guided roles need this

        graph = load_graph(self.store.index_dir())
        if graph is None:
            return []

        files = sorted({n.file for n in graph.nodes if n.file})
        file_set = set(files)
        seen = set(request.referenced_paths)
        task_tokens = _tokenize(request.task)

        items: list[ContextItem] = []

        for f in self._expand_dependencies(graph, request.referenced_paths, file_set):
            if f in seen:
                continue
            seen.add(f)
            items.append(ContextItem(
                key=f, layer=3, priority=7, volatility=4,
                reason="imported by a referenced file", content=None,
            ))

        for f in files:
            if f in seen:
                continue
            seen.add(f)
            matched = task_tokens & _tokenize(f)
            if matched:
                items.append(ContextItem(
                    key=f, layer=3, priority=8, volatility=4,
                    reason=f"present in the codebase index (matches task keyword {sorted(matched)[0]!r})",
                    content=None,
                ))
            else:
                items.append(ContextItem(
                    key=f, layer=3, priority=9, volatility=4,
                    reason="present in the codebase index", content=None,
                ))
        return items


class DiffProvider:
    """The real git diff of the builder's changes to the project source,
    for the reviewer/qa roles (docs/plan.md: QA's context should be "the
    git diff", named directly alongside the acceptance criteria).

    Embedded content, not a reference: unlike the file inventory, a diff
    isn't something the agent should have to go re-read with its own
    tools -- it's the one thing every downstream role in this state
    definitely needs, so it's handed over whole. Priority 4, alongside
    `ArtifactSectionProvider` -- the diff is this role's working artifact,
    functionally.

    Reads `state.yaml`'s `implementation_base_revision` (set once by
    `TurnRunner` on the first turn of the `implementation` state) and
    diffs the project source from there to its current commit via
    `orchestrator/project_git.py`. Degrades to nothing -- not a failure --
    whenever there's nothing to show: no project source configured, the
    source isn't a git repo, no base revision recorded yet, or the diff
    itself is empty (a builder turn that produced no source changes,
    which is an outcome, not a bug in this provider).
    """

    name = "diff"

    def __init__(self, store: ProjectStore, project_source_root: Path | str | None):
        self.store = store
        self.project_source_root = Path(project_source_root) if project_source_root else None

    def collect(self, request: BuildRequest) -> list[ContextItem]:
        if self.project_source_root is None:
            return []

        from .. import project_git  # local: only guided review/qa roles need this

        if not project_git.is_git_repo(self.project_source_root):
            return []

        state_data = self.store.read_state()
        if "implementation_base_revision" not in state_data:
            return []  # the implementation state hasn't captured one yet

        text = project_git.diff(self.project_source_root, state_data["implementation_base_revision"])
        if not text.strip():
            return []

        return [ContextItem(
            key="git-diff", layer=3, priority=4, volatility=4,
            reason="the builder's actual changes to the project source",
            content=text,
        )]
