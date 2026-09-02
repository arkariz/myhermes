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
from ..domain.workflow import State, WorkflowDefinition

# config/workflow.yaml's two conventional `backlog` approval types --
# named here, once, rather than as a string literal repeated at every
# call site (entrypoints/cli.py's --import, entrypoints/telegram/
# handlers.py's plain /create and /import both need exactly these).
START_PROJECT_APPROVAL = "START_PROJECT"
IMPORT_PROJECT_APPROVAL = "IMPORT_PROJECT"


def leading_gate_approval_type(state: State) -> str | None:
    """Which approval clears `state` (normally `workflow.initial`,
    i.e. `backlog`) on the ordinary "start a new project" path.

    Old-style single `approval_type` states return it directly. A
    State.next_by_approval state (backlog now has two: START_PROJECT and
    IMPORT_PROJECT) returns START_PROJECT specifically IF it's one of the
    declared options -- never guesses at some other key. None means "don't
    auto-clear this gate", which is the honest answer for a workflow whose
    leading gate doesn't use either convention; the caller then leaves it
    for a human to `/approve` explicitly rather than firing the wrong
    typed event.
    """
    if state.approval_type:
        return state.approval_type
    if state.next_by_approval and START_PROJECT_APPROVAL in state.next_by_approval:
        return START_PROJECT_APPROVAL
    return None


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
