"""Session boundary triggers.

This is the highest-value suite in the project: a missed trigger means stale
context silently leaks into a state that never asked for it, and nothing else
would catch that. Every trigger gets its own positive test (it alone causes a
rebuild) and there is one negative test proving an unrelated field change does
NOT cause a rebuild.
"""

from datetime import datetime, timedelta, timezone

import pytest

from agentic_dev.domain.sessions import (
    BoundaryReason,
    Session,
    SessionManager,
    SessionPolicy,
)
from agentic_dev.domain.workflow import State, StateKind

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

COLLAB = State(
    name="planning", kind=StateKind.COLLABORATIVE, role="planner", next="product-design"
)
AUTONOMOUS = State(
    name="implementation", kind=StateKind.AUTONOMOUS, role="builder", next="review"
)


def base_session(**overrides) -> Session:
    defaults = dict(
        hermes_session_id="sess-1",
        role="planner",
        state="planning",
        opened_at_turn="planning-01",
        opened_at=NOW.isoformat(),
        turns=1,
        artifact_revision=1,
        decision_revision=0,
        index_revision="rev-a",
    )
    defaults.update(overrides)
    return Session(**defaults)


def decide(manager: SessionManager, session, state=COLLAB, **kw):
    params = dict(
        session=session,
        state=state,
        role="planner",
        artifact_revision=1,
        decision_revision=0,
        index_revision="rev-a",
        now=NOW,
    )
    params.update(kw)
    return manager.decide(**params)


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager(SessionPolicy(max_session_turns=12, max_session_age_minutes=120))


# ---- the eight triggers, each in isolation --------------------------------


def test_no_session_forces_full(manager):
    d = decide(manager, None)
    assert d.resume is False
    assert d.reason is BoundaryReason.NO_SESSION
    assert d.kind == "full"


def test_state_change_forces_full(manager):
    d = decide(manager, base_session(state="discovery"))
    assert d.reason is BoundaryReason.STATE_CHANGED


def test_role_change_forces_full(manager):
    d = decide(manager, base_session(role="architect"))
    assert d.reason is BoundaryReason.ROLE_CHANGED


def test_artifact_revision_bump_forces_full(manager):
    d = decide(manager, base_session(artifact_revision=1), artifact_revision=2)
    assert d.reason is BoundaryReason.ARTIFACT_REVISION_BUMPED


def test_decision_recorded_forces_full(manager):
    d = decide(manager, base_session(decision_revision=0), decision_revision=1)
    assert d.reason is BoundaryReason.DECISION_RECORDED


def test_index_revision_change_forces_full(manager):
    d = decide(manager, base_session(index_revision="rev-a"), index_revision="rev-b")
    assert d.reason is BoundaryReason.INDEX_REVISION_CHANGED


def test_session_turn_cap_forces_full(manager):
    d = decide(manager, base_session(turns=12))
    assert d.reason is BoundaryReason.SESSION_EXHAUSTED


def test_session_age_cap_forces_full(manager):
    stale_open = (NOW - timedelta(minutes=121)).isoformat()
    d = decide(manager, base_session(opened_at=stale_open))
    assert d.reason is BoundaryReason.SESSION_EXHAUSTED


def test_previous_turn_failed_forces_full(manager):
    d = decide(manager, base_session(last_turn_failed=True))
    assert d.reason is BoundaryReason.PREVIOUS_TURN_FAILED


def test_human_refresh_forces_full(manager):
    d = decide(manager, base_session(refresh_requested=True))
    assert d.reason is BoundaryReason.HUMAN_REFRESH


def test_autonomous_state_never_resumes(manager):
    # Autonomous turns are decision points; they always rebuild regardless of
    # how fresh and matching the session otherwise is.
    d = decide(manager, base_session(), state=AUTONOMOUS, role="builder")
    assert d.reason is BoundaryReason.STATE_FORBIDS_CONTINUATION


def test_unparseable_timestamp_is_treated_as_stale(manager):
    d = decide(manager, base_session(opened_at="not-a-date"))
    assert d.reason is BoundaryReason.SESSION_EXHAUSTED


# ---- the negative case: nothing else invalidates ---------------------------


def test_matching_session_within_limits_resumes(manager):
    session = base_session(turns=3)
    d = decide(manager, session)
    assert d.resume is True
    assert d.reason is None
    assert d.session_id == "sess-1"
    assert d.kind == "delta"


def test_turn_count_just_under_cap_still_resumes(manager):
    session = base_session(turns=11)
    d = decide(manager, session)
    assert d.resume is True


def test_age_just_under_cap_still_resumes(manager):
    fresh_open = (NOW - timedelta(minutes=119)).isoformat()
    d = decide(manager, base_session(opened_at=fresh_open))
    assert d.resume is True


# ---- mutation --------------------------------------------------------------


def test_open_creates_a_fresh_session_at_turn_one(manager):
    session = manager.open(
        hermes_session_id="new-sess",
        role="planner",
        state="planning",
        turn_id="planning-01",
        artifact_revision=1,
        decision_revision=0,
        index_revision="rev-a",
        now=NOW,
    )
    assert session.turns == 1
    assert session.opened_at_turn == "planning-01"


def test_record_turn_increments_and_tracks_failure(manager):
    session = base_session(turns=1)
    manager.record_turn(session, failed=True)
    assert session.turns == 2
    assert session.last_turn_failed is True


def test_profile_scope_role_project_isolates_per_project():
    policy = SessionPolicy(profile_scope="role_project")
    assert policy.profile_name("planner", "bonked") == "ad-planner-bonked"
    assert policy.profile_name("planner", "babylang") == "ad-planner-babylang"


def test_profile_scope_role_shares_across_projects():
    # This is now the DEFAULT, not a fallback -- verified by spike measurement
    # 3 that HERMES_PROFILE does not isolate memory, so sharing a profile
    # name across projects costs nothing extra (home_dir does the isolating).
    policy = SessionPolicy(profile_scope="role")
    assert policy.profile_name("planner", "bonked") == policy.profile_name(
        "planner", "babylang"
    )


def test_default_profile_scope_is_role_not_role_project():
    # An earlier design defaulted to role_project, assuming HERMES_PROFILE
    # was an isolation boundary. Spike measurement 3 disproved that: two
    # profiles under one HERMES_HOME both recalled the same planted secret.
    policy = SessionPolicy()
    assert policy.profile_scope == "role"


def test_home_dir_is_unique_per_project():
    # The verified isolation boundary. Every runtime invocation for a
    # project MUST use this HERMES_HOME -- profile name alone is not enough.
    policy = SessionPolicy()
    assert policy.home_dir("bonked") != policy.home_dir("babylang")
    assert "bonked" in policy.home_dir("bonked")


def test_home_dir_is_stable_for_the_same_project():
    policy = SessionPolicy()
    assert policy.home_dir("bonked") == policy.home_dir("bonked")
