"""Event log: every CONTEXT_BUILT records both inclusions and omissions with
reasons, and token drift is computed honestly against the real usage report."""

from agentic_dev.adapters.storage.events import EventLog, EventType
from agentic_dev.adapters.storage.store import ProjectStore


def test_context_built_records_selection_and_omission_reasons(tmp_path):
    store = ProjectStore(tmp_path)
    log = EventLog(store, "toy")
    log.context_built(
        workflow_state="planning", turn_id="planning-01", role="planner",
        mode="assembled", kind="full", estimated_tokens=500,
        manifest_keys=["souls/planner.md"],
        omitted=[("artifacts/huge.md", "budget exceeded")],
        source_revision="abc123", index_revision=None,
        model="claude-sonnet-4.6", provider="openrouter",
    )
    events = list(store.read_events())
    assert events[0]["type"] == EventType.CONTEXT_BUILT.value
    assert events[0]["payload"]["selected"] == ["souls/planner.md"]
    assert events[0]["payload"]["omitted"][0]["reason"] == "budget exceeded"


def test_turn_completed_computes_token_drift(tmp_path):
    store = ProjectStore(tmp_path)
    log = EventLog(store, "toy")
    log.turn_completed(
        workflow_state="planning", turn_id="planning-01", session_id="sess-1",
        usage={"input_tokens": 1000}, estimated_tokens=1100,
    )
    events = list(store.read_events())
    assert events[0]["payload"]["token_drift"] == 0.1


def test_turn_completed_handles_missing_usage_without_crashing(tmp_path):
    store = ProjectStore(tmp_path)
    log = EventLog(store, "toy")
    log.turn_completed(
        workflow_state="planning", turn_id="planning-01", session_id=None,
        usage={}, estimated_tokens=500,
    )
    events = list(store.read_events())
    assert events[0]["payload"]["token_drift"] is None
