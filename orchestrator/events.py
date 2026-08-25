"""Typed workflow events.

Every significant transition is appended to events.jsonl. The log is the
audit trail: it must be possible to reconstruct what happened, what the model
was sent, and why, from this file alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from .store import ProjectStore, utcnow


class EventType(str, Enum):
    PROJECT_CREATED = "PROJECT_CREATED"
    STATE_CHANGED = "STATE_CHANGED"

    AGENT_TURN_STARTED = "AGENT_TURN_STARTED"
    AGENT_TURN_COMPLETED = "AGENT_TURN_COMPLETED"
    AGENT_TURN_FAILED = "AGENT_TURN_FAILED"

    HUMAN_MESSAGE_RECEIVED = "HUMAN_MESSAGE_RECEIVED"
    HUMAN_FEEDBACK_RECEIVED = "HUMAN_FEEDBACK_RECEIVED"

    ARTIFACT_UPDATED = "ARTIFACT_UPDATED"
    SOURCE_COMMITTED = "SOURCE_COMMITTED"
    DECISION_RECORDED = "DECISION_RECORDED"
    SUMMARY_UPDATED = "SUMMARY_UPDATED"

    CONTEXT_BUILT = "CONTEXT_BUILT"
    CONTEXT_DENYLIST_VIOLATION = "CONTEXT_DENYLIST_VIOLATION"

    SESSION_OPENED = "SESSION_OPENED"
    SESSION_INVALIDATED = "SESSION_INVALIDATED"

    INDEX_STARTED = "INDEX_STARTED"
    INDEX_COMPLETED = "INDEX_COMPLETED"
    INDEX_INVALIDATED = "INDEX_INVALIDATED"

    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"

    QA_PASSED = "QA_PASSED"
    QA_FAILED = "QA_FAILED"

    JOB_FAILED = "JOB_FAILED"
    JOB_RETRYING = "JOB_RETRYING"
    PROJECT_BLOCKED = "PROJECT_BLOCKED"


@dataclass
class Event:
    type: EventType
    project_id: str
    workflow_state: str | None = None
    turn_id: str | None = None
    session_id: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


class EventLog:
    """Append-only event writer bound to one project."""

    def __init__(self, store: ProjectStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def emit(self, type: EventType, **kwargs: Any) -> Event:
        event = Event(type=type, project_id=self.project_id, **kwargs)
        self.store.append_event(event.to_dict())
        return event

    def context_built(
        self,
        *,
        workflow_state: str,
        turn_id: str,
        role: str,
        mode: str,
        kind: str,
        estimated_tokens: int,
        manifest_keys: list[str],
        omitted: list[tuple[str, str]],
        source_revision: str | None,
        index_revision: str | None,
        model: str | None,
        provider: str | None,
    ) -> Event:
        """CONTEXT_BUILT carries everything needed to explain a model call.

        Recording the omissions matters as much as the inclusions -- "why was
        this file left out" is a question the system must be able to answer.
        """
        return self.emit(
            EventType.CONTEXT_BUILT,
            workflow_state=workflow_state,
            turn_id=turn_id,
            payload={
                "role": role,
                "mode": mode,
                "kind": kind,
                "estimated_tokens": estimated_tokens,
                "selected": manifest_keys,
                "omitted": [{"key": k, "reason": r} for k, r in omitted],
                "source_revision": source_revision,
                "index_revision": index_revision,
                "model": model,
                "provider": provider,
            },
        )

    def turn_completed(
        self,
        *,
        workflow_state: str,
        turn_id: str,
        session_id: str | None,
        usage: dict[str, Any],
        estimated_tokens: int,
        rtk_ratio: float | None = None,
    ) -> Event:
        """Record real usage alongside our estimate.

        token_drift is kept explicitly: our tiktoken count is an approximation
        across providers, and silently trusting it would make every budget
        claim unfalsifiable.
        """
        actual = usage.get("input_tokens")
        drift = None
        if isinstance(actual, int) and actual > 0:
            drift = round((estimated_tokens - actual) / actual, 4)
        return self.emit(
            EventType.AGENT_TURN_COMPLETED,
            workflow_state=workflow_state,
            turn_id=turn_id,
            session_id=session_id,
            payload={
                "usage": usage,
                "estimated_tokens": estimated_tokens,
                "token_drift": drift,
                "rtk_compression_ratio": rtk_ratio,
            },
        )
