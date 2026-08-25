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
