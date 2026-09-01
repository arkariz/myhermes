"""Register a project and give it a real, ready-to-write state.yaml -- the
one piece `entrypoints/cli.py`'s `project new` and `entrypoints/telegram/
handlers.py`'s `/create` genuinely duplicate.

Deliberately narrow: everything downstream of this (Telegram topic
creation, the PROJECT_CREATED event, auto-clearing a leading gate state)
differs enough between an operator-run CLI and a chat-driven flow that
pulling those in too would just be indirection with extra parameters to
thread through -- see app/approval_flow.py's own docstring for the same
argument about where a shared helper's boundary belongs.
"""

from __future__ import annotations

from ..adapters.registry import ProjectEntry, ProjectRegistry
from ..adapters.storage.store import ProjectStore
from ..domain.workflow import WorkflowDefinition


def create_project(
    registry: ProjectRegistry,
    *,
    name: str,
    host_path: str,
    state_path: str,
    workflow: WorkflowDefinition,
    platform: str = "flutter",
) -> tuple[ProjectEntry, ProjectStore]:
    entry = registry.register(name, host_path=host_path, state_path=state_path, platform=platform)
    store = ProjectStore(entry.state_path)
    store.ensure_layout()
    store.write_state({"workflow_state": workflow.initial, "attempts": 0})
    return entry, store
