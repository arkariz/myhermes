"""Human input semantics (brief S31) and decision-block parsing (S16).

The rule that matters: free text is ALWAYS feedback. An approval event exists
only when an inline-button callback or an exact command arrives WHILE the
project has a matching pending_action. "looks good", "yes", "approve" typed
at the wrong moment must never advance a gate -- the workflow engine holds the
pending question, not the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

APPROVE_COMMAND = re.compile(r"^/approve\b\s*(\S+)?\s*$", re.IGNORECASE)
DECISION_BLOCK = re.compile(r"```decision\s*\n(.*?)```", re.DOTALL)


class ApprovalError(Exception):
    """Raised when an approval is attempted without a matching pending action."""


@dataclass(frozen=True)
class PendingAction:
    type: str                 # "approval" | "question" | None
    approval_type: str | None = None
    # Several valid approval types instead of one exact match -- a state
    # with State.next_by_approval (its destination depends on WHICH typed
    # approval a human gives, not on anything an agent decides). None for
    # every ordinary single-approval state; resolve_command/resolve_callback
    # fall back to `approval_type` alone when this is unset.
    approval_types: tuple[str, ...] | None = None
    artifact: str | None = None
    artifact_revision: int | None = None

    @classmethod
    def none(cls) -> "PendingAction":
        return cls(type="none")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PendingAction":
        if not d:
            return cls.none()
        approval_types = d.get("approval_types")
        return cls(
            type=d.get("type", "none"),
            approval_type=d.get("approval_type"),
            approval_types=tuple(approval_types) if approval_types else None,
            artifact=d.get("artifact"),
            artifact_revision=d.get("artifact_revision"),
        )


@dataclass(frozen=True)
class ApprovalEvent:
    artifact: str | None
    artifact_revision: int | None
    approval_type: str
    source_turn: str | None
    approver: str
    timestamp: str


def resolve_free_text(text: str, pending: PendingAction) -> dict[str, Any]:
    """Free-form text is always feedback -- it can never become an approval,
    no matter how it reads. "yes", "approve", "looks good" are all just
    HUMAN_FEEDBACK_RECEIVED payloads."""
    return {"kind": "feedback", "text": text}


def _resolve_requested_type(requested: str | None, pending: PendingAction) -> str:
    """Match a requested (possibly absent) approval type against everything
    `pending` actually accepts -- `approval_types` (several valid, from a
    State.next_by_approval state) when set, else the single `approval_type`
    every ordinary state uses. Raises ApprovalError on no match, or on an
    omitted type when more than one would be valid (nothing to default to)."""
    valid = pending.approval_types or (
        (pending.approval_type,) if pending.approval_type else ()
    )
    if not valid:
        raise ApprovalError("no approval type is pending to confirm")

    if requested is None:
        if len(valid) == 1:
            return valid[0]
        raise ApprovalError(
            f"pending approval is one of {sorted(valid)}; specify which type"
        )

    matches = [t for t in valid if t.upper() == requested.upper()]
    if not matches:
        raise ApprovalError(f"pending approval is one of {sorted(valid)}, got {requested!r}")
    return matches[0]


def resolve_command(
    text: str, pending: PendingAction, *, approver: str, source_turn: str | None,
    now: str,
) -> dict[str, Any]:
    """Resolve an explicit `/approve [TYPE]` command.

    Only produces an approval if pending.type == "approval" AND the command's
    type (if given) matches one of pending's valid types exactly. Everything
    else is feedback -- including a bare "/approve" typed while nothing is
    pending, or while more than one type would be valid and none was named.
    """
    match = APPROVE_COMMAND.match(text.strip())
    if not match:
        return resolve_free_text(text, pending)

    if pending.type != "approval":
        raise ApprovalError("no approval is pending; /approve has nothing to confirm")

    resolved_type = _resolve_requested_type(match.group(1), pending)

    return {
        "kind": "approval",
        "event": ApprovalEvent(
            artifact=pending.artifact,
            artifact_revision=pending.artifact_revision,
            approval_type=resolved_type,
            source_turn=source_turn,
            approver=approver,
            timestamp=now,
        ),
    }


def resolve_callback(
    callback_approval_type: str,
    callback_artifact_revision: int | None,
    pending: PendingAction,
    *,
    approver: str,
    source_turn: str | None,
    now: str,
) -> dict[str, Any]:
    """Resolve an inline-button callback.

    A callback referencing a stale artifact revision is rejected -- the human
    was looking at an outdated message and the artifact has since moved on.
    """
    if pending.type != "approval":
        raise ApprovalError("no approval is pending; this button is stale")

    resolved_type = _resolve_requested_type(callback_approval_type, pending)

    if (
        pending.artifact_revision is not None
        and callback_artifact_revision != pending.artifact_revision
    ):
        raise ApprovalError(
            f"stale artifact revision: button references "
            f"{callback_artifact_revision}, current is {pending.artifact_revision}"
        )

    return {
        "kind": "approval",
        "event": ApprovalEvent(
            artifact=pending.artifact,
            artifact_revision=pending.artifact_revision,
            approval_type=resolved_type,
            source_turn=source_turn,
            approver=approver,
            timestamp=now,
        ),
    }


def parse_decision_blocks(agent_output: str) -> list[dict[str, Any]]:
    """Extract ```decision fenced YAML blocks from agent output.

    Deterministic parsing, not a second LLM call -- an agent that wants to
    record a decision emits this fence and the orchestrator trusts the
    structure, not the prose around it.
    """
    decisions = []
    for match in DECISION_BLOCK.finditer(agent_output):
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            # A malformed block must not crash the turn -- best-effort capture.
            continue
        if isinstance(block, dict):
            decisions.append(block)
    return decisions
