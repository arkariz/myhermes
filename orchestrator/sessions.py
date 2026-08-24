"""Hermes session lifecycle -- the hybrid memory model.

Two memory systems with different jobs:

  Hermes session   "what did we just say"      one workflow state, opaque
  agent-state      "what the project believes" permanent, inspectable

Within a collaborative state we resume the Hermes session (`--resume <id>`)
and send only a delta, which is cheap and keeps the provider's prefix cache
warm (both verified live against OpenRouter: a resumed session genuinely
recalls prior turns, and cache_read_tokens is non-zero on a repeated stable
prefix). At a boundary we open a fresh session and rebuild the full context
with a complete manifest.

Getting the boundary set wrong is the dangerous failure: a missed trigger means
stale context leaks silently into a new state, and nothing surfaces the error.
Triggers are therefore enumerated explicitly and tested one by one.

SPIKE FINDING -- a THIRD, uncontrolled memory channel exists and is not a
workflow-state boundary at all: Hermes runs a background "self-improvement"
writer that saves user statements to ~/.hermes/memories/MEMORY.md and
re-injects it into every subsequent turn, regardless of --ignore-rules,
--safe-mode, or memory.memory_enabled: false -- verified live, not assumed
from docs. That file is scoped to HERMES_HOME, NOT to HERMES_PROFILE: two
different profiles under one HERMES_HOME both recalled the same planted
secret. The only verified isolation boundary is a separate HERMES_HOME
directory per project (SessionPolicy.home_dir), which every runtime
invocation for that project must use. Profiles remain useful for
role-scoped model/skills/toolset config, just not for this.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .state_machine import State


class BoundaryReason(str, Enum):
    """Why a session was invalidated. One member per trigger, so a test can
    assert the full set is covered and no trigger is silently dropped."""

    NO_SESSION = "no_session"
    STATE_CHANGED = "state_changed"
    ROLE_CHANGED = "role_changed"
    ARTIFACT_REVISION_BUMPED = "artifact_revision_bumped"
    DECISION_RECORDED = "decision_recorded"
    INDEX_REVISION_CHANGED = "index_revision_changed"
    SESSION_EXHAUSTED = "session_exhausted"
    PREVIOUS_TURN_FAILED = "previous_turn_failed"
    HUMAN_REFRESH = "human_refresh"
    STATE_FORBIDS_CONTINUATION = "state_forbids_continuation"


@dataclass
class Session:
    """A live Hermes conversation, scoped to one project + workflow state."""

    hermes_session_id: str
    role: str
    state: str
    opened_at_turn: str
    opened_at: str
    turns: int = 0
    artifact_revision: int = 0
    decision_revision: int = 0
    index_revision: str | None = None
    last_turn_failed: bool = False
    refresh_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Session | None":
        if not d or not d.get("hermes_session_id"):
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class SessionPolicy:
    max_session_turns: int = 12
    max_session_age_minutes: int = 120
    profile_scope: str = "role"
    home_scope: str = "project"
    home_path_template: str = "/workspace/agent-state/{project_id}/.hermes-home"

    def profile_name(self, role: str, project_id: str) -> str:
        """Hermes profile name.

        NOT an isolation boundary -- verified by spike measurement 3.
        Hermes's background memory writer persists to
        ~/.hermes/memories/MEMORY.md, scoped to HERMES_HOME, and two
        different profiles under one HERMES_HOME both recalled the same
        planted secret in testing. Profiles only separate model/skills/
        toolset config; use `home_dir` for the isolation guarantee.
        """
        if self.profile_scope == "role_project":
            return f"ad-{role}-{project_id}"
        return f"ad-{role}"

    def home_dir(self, project_id: str) -> str:
        """The HERMES_HOME each invocation for this project MUST use.

        This is the real isolation boundary. Every runtime call for a given
        project sets HERMES_HOME to this path (mounted as a distinct volume
        per project), so MEMORY.md and the sessions DB never span projects.
        Mandatory, not an optimization -- omitting it lets one project's
        content leak into another's context.
        """
        return self.home_path_template.format(project_id=project_id)


@dataclass(frozen=True)
class SessionDecision:
    resume: bool
    session_id: str | None
    reason: BoundaryReason | None

    @property
    def kind(self) -> str:
        return "delta" if self.resume else "full"


class SessionManager:
    """Decides, for each turn, whether to resume or start clean."""

    def __init__(self, policy: SessionPolicy):
        self.policy = policy

    def decide(
        self,
        *,
        session: Session | None,
        state: State,
        role: str,
        artifact_revision: int,
        decision_revision: int,
        index_revision: str | None,
        now: datetime | None = None,
    ) -> SessionDecision:
        """Resume only if every boundary check passes.

        Ordering is deliberate: the cheapest and most decisive checks run
        first, and the first failure wins so the recorded reason is the most
        specific one available.
        """
        reason = self._boundary_reason(
            session=session,
            state=state,
            role=role,
            artifact_revision=artifact_revision,
            decision_revision=decision_revision,
            index_revision=index_revision,
            now=now or datetime.now(timezone.utc),
        )
        if reason is not None:
            return SessionDecision(resume=False, session_id=None, reason=reason)
        assert session is not None
        return SessionDecision(
            resume=True, session_id=session.hermes_session_id, reason=None
        )

    def _boundary_reason(
        self,
        *,
        session: Session | None,
        state: State,
        role: str,
        artifact_revision: int,
        decision_revision: int,
        index_revision: str | None,
        now: datetime,
    ) -> BoundaryReason | None:
        if session is None:
            return BoundaryReason.NO_SESSION

        # Autonomous turns and gates always rebuild: they are decision points
        # and must carry a complete, reproducible manifest.
        if not state.allows_continuation:
            return BoundaryReason.STATE_FORBIDS_CONTINUATION

        if session.refresh_requested:
            return BoundaryReason.HUMAN_REFRESH

        if session.last_turn_failed:
            return BoundaryReason.PREVIOUS_TURN_FAILED

        if session.state != state.name:
            return BoundaryReason.STATE_CHANGED

        if session.role != role:
            return BoundaryReason.ROLE_CHANGED

        # An approval bumps the artifact revision, so this covers both
        # "approval granted" and "artifact rewritten" with one comparison.
        if session.artifact_revision != artifact_revision:
            return BoundaryReason.ARTIFACT_REVISION_BUMPED

        if session.decision_revision != decision_revision:
            return BoundaryReason.DECISION_RECORDED

        if session.index_revision != index_revision:
            return BoundaryReason.INDEX_REVISION_CHANGED

        if session.turns >= self.policy.max_session_turns:
            return BoundaryReason.SESSION_EXHAUSTED

        if self._is_stale(session, now):
            return BoundaryReason.SESSION_EXHAUSTED

        return None

    def _is_stale(self, session: Session, now: datetime) -> bool:
        try:
            opened = datetime.fromisoformat(session.opened_at)
        except (ValueError, TypeError):
            # An unparseable timestamp is treated as stale. Rebuilding costs
            # tokens; resuming on a corrupt session risks wrong context.
            return True
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        age = now - opened
        return age >= timedelta(minutes=self.policy.max_session_age_minutes)

    # ---- mutation --------------------------------------------------------

    def open(
        self,
        *,
        hermes_session_id: str,
        role: str,
        state: str,
        turn_id: str,
        artifact_revision: int,
        decision_revision: int,
        index_revision: str | None,
        now: datetime | None = None,
    ) -> Session:
        return Session(
            hermes_session_id=hermes_session_id,
            role=role,
            state=state,
            opened_at_turn=turn_id,
            opened_at=(now or datetime.now(timezone.utc)).isoformat(),
            turns=1,
            artifact_revision=artifact_revision,
            decision_revision=decision_revision,
            index_revision=index_revision,
        )

    def record_turn(self, session: Session, *, failed: bool = False) -> Session:
        session.turns += 1
        session.last_turn_failed = failed
        return session
