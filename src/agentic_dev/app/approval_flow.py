"""Applies one approval end to end -- shared by every human-input surface.

CLI and Telegram both need to: read the current pending action, validate the
incoming approval against it (§31 semantics, from approvals.py), advance the
state machine, and persist the result. Before this module existed that logic
was written once in `cli.cmd_approve`; adding Telegram without extracting it
would have meant a second hand-written copy that could silently drift from
the first. One function, two callers.

Two validation shapes, one persistence tail:

  * A typed `/approve TYPE` command (CLI, or a Telegram slash command) has no
    notion of "what the human was looking at" -- resolve_command() just
    checks it matches the current pending action.
  * An inline-button callback (Telegram) was rendered against a specific
    artifact_revision at send time. Passing that revision as
    `callback_artifact_revision` routes through resolve_callback() instead,
    which rejects a stale click -- the plan's own acceptance criterion
    ("a callback with a stale artifact revision is rejected").
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.approvals import PendingAction, resolve_callback, resolve_command
from ..domain.workflow import WorkflowDefinition
from ..adapters.storage.store import ProjectStore


def apply_approval(
    store: ProjectStore,
    workflow: WorkflowDefinition,
    approval_type: str,
    *,
    approver: str,
    callback_artifact_revision: int | None = None,
) -> str:
    """Validate and apply one approval. Returns the new workflow_state.

    Raises ApprovalError (no/mismatched/stale pending action) or
    WorkflowError (approval type doesn't match what the state declares) --
    both are the caller's to catch and surface, not swallowed here.
    """
    state_data = store.read_state()
    current = state_data.get("workflow_state", workflow.initial)
    state = workflow.get(current)
    now = datetime.now(timezone.utc).isoformat()

    pending = PendingAction(
        type="approval" if state.requires_approval else "none",
        approval_type=state.approval_type,
        artifact=state.artifact,
        artifact_revision=state_data.get("artifact_revision", 0),
    )

    if callback_artifact_revision is not None:
        resolve_callback(
            approval_type, callback_artifact_revision, pending,
            approver=approver, source_turn=None, now=now,
        )
    else:
        resolve_command(
            f"/approve {approval_type}", pending, approver=approver,
            source_turn=None, now=now,
        )

    next_state = workflow.advance(current, approval_type=approval_type)
    store.update_state(
        workflow_state=next_state, attempts=0,
        artifact_revision=state_data.get("artifact_revision", 0) + 1,
        session=None,  # force a full rebuild in the new state
    )
    store.append_event({
        "type": "APPROVAL_GRANTED",
        "workflow_state": current,
        "timestamp": now,
        "payload": {"approval_type": approval_type, "next_state": next_state, "approver": approver},
    })
    return next_state


def default_continuation_message(approval_type: str, next_state: str) -> str:
    """The synthetic human message used to auto-kick-off a state's first
    turn right after its own gating approval, so a human doesn't have to
    type something purely to unlock work the approval already authorized
    (docs/progress.md: "why did I have to chat manually after approving").

    Deliberately content-light: the role's own context (running summary,
    current artifact, recorded decisions) already carries everything
    relevant to continue with -- this message only needs to say "go", not
    restate the plan.

    NOT used for a project's very first approval (the implicit backlog ->
    discovery one at project creation) -- discovery's first turn needs the
    human's own initial idea as its content, which nothing here can
    synthesize. Only CLI's `approve` command and Telegram's inline-button
    callback call this; `_create_project_from_chat`'s auto-approval does
    not.
    """
    return f"{approval_type} approved -- continue with {next_state}."
