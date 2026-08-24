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
    artifact: str | None = None
    artifact_revision: int | None = None

    @classmethod
    def none(cls) -> "PendingAction":
        return cls(type="none")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PendingAction":
        if not d:
            return cls.none()
        return cls(
            type=d.get("type", "none"),
            approval_type=d.get("approval_type"),
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


def resolve_command(
    text: str, pending: PendingAction, *, approver: str, source_turn: str | None,
    now: str,
) -> dict[str, Any]:
    """Resolve an explicit `/approve [TYPE]` command.

    Only produces an approval if pending.type == "approval" AND the command's
    type (if given) matches pending.approval_type exactly. Everything else is
    feedback -- including a bare "/approve" typed while nothing is pending.
    """
    match = APPROVE_COMMAND.match(text.strip())
    if not match:
        return resolve_free_text(text, pending)

    if pending.type != "approval":
        raise ApprovalError("no approval is pending; /approve has nothing to confirm")

    requested = match.group(1)
    if requested and requested.upper() != (pending.approval_type or "").upper():
        raise ApprovalError(
            f"pending approval is {pending.approval_type!r}, got {requested!r}"
        )

    return {
        "kind": "approval",
        "event": ApprovalEvent(
            artifact=pending.artifact,
            artifact_revision=pending.artifact_revision,
            approval_type=pending.approval_type,  # type: ignore[arg-type]
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

    if callback_approval_type != pending.approval_type:
        raise ApprovalError(
            f"pending approval is {pending.approval_type!r}, "
            f"button was for {callback_approval_type!r}"
        )

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
            approval_type=pending.approval_type,  # type: ignore[arg-type]
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
