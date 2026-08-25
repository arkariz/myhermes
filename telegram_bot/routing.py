"""Forum topic/thread -> project_id routing.

So a human never has to prefix messages with a project name inside
Telegram: they `/link <project_id>` a chat (or a forum topic within it)
once, and every plain-text message there after is routed to that project's
turns.

Routing lives on the project's own registry entry (`ProjectRegistry.
link_telegram`), not a separate mapping file -- a project already has
exactly one canonical identity, and registry.py's own docstring names this
as exactly the Phase 2 extension point ("routing... is added to an entry as
Phase 2/3 need it, not speculatively here").
"""

from __future__ import annotations

from orchestrator.registry import ProjectEntry, ProjectRegistry


class RoutingError(Exception):
    """No project is linked to this chat/topic yet."""


def resolve_project(
    registry: ProjectRegistry, *, chat_id: int, thread_id: int | None,
) -> ProjectEntry:
    entry = registry.find_by_telegram(chat_id=chat_id, thread_id=thread_id)
    if entry is None:
        raise RoutingError(
            "No project is linked to this chat/topic yet. "
            "Use `/link <project_id>` first."
        )
    return entry


def link_project(
    registry: ProjectRegistry, project_id: str, *, chat_id: int, thread_id: int | None,
) -> ProjectEntry:
    return registry.link_telegram(project_id, chat_id=chat_id, thread_id=thread_id)
