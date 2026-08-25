"""apply_approval -- the shared §31 validation + persistence path used by
both cli.cmd_approve and (Phase 2) telegram/handlers.py. Covers both
resolution shapes: a typed command (no revision to check) and an inline
callback (must reject a stale artifact_revision)."""

import pytest

from orchestrator.approval_flow import apply_approval
from orchestrator.approvals import ApprovalError
from orchestrator.state_machine import WorkflowDefinition
from orchestrator.store import ProjectStore

WORKFLOW_YAML = """
initial: planning
states:
  planning:
    kind: collaborative
    role: planner
    artifact: prd.md
    completion: approval
    approval_type: APPROVE_PRD
    next: done
  done:
    kind: terminal
"""


@pytest.fixture
def store_and_workflow(tmp_path):
    (tmp_path / "workflow.yaml").write_text(WORKFLOW_YAML)
    workflow = WorkflowDefinition.load(tmp_path / "workflow.yaml")
    store = ProjectStore(tmp_path / "state")
    store.ensure_layout()
    store.write_state({"workflow_state": "planning", "attempts": 0, "artifact_revision": 3})
    return store, workflow


def test_command_approval_advances_state(store_and_workflow):
    store, workflow = store_and_workflow
    next_state = apply_approval(store, workflow, "APPROVE_PRD", approver="cli")
    assert next_state == "done"
    assert store.read_state()["workflow_state"] == "done"
    assert store.read_state()["artifact_revision"] == 4


def test_command_approval_with_wrong_type_raises(store_and_workflow):
    store, workflow = store_and_workflow
    # resolve_command() catches the mismatch before workflow.advance() would.
    with pytest.raises(ApprovalError):
        apply_approval(store, workflow, "APPROVE_MERGE", approver="cli")


def test_callback_approval_with_current_revision_advances(store_and_workflow):
    store, workflow = store_and_workflow
    next_state = apply_approval(
        store, workflow, "APPROVE_PRD", approver="telegram:42",
        callback_artifact_revision=3,
    )
    assert next_state == "done"


def test_callback_approval_with_stale_revision_is_rejected(store_and_workflow):
    store, workflow = store_and_workflow
    with pytest.raises(ApprovalError):
        apply_approval(
            store, workflow, "APPROVE_PRD", approver="telegram:42",
            callback_artifact_revision=1,  # human clicked a button from an older message
        )
    assert store.read_state()["workflow_state"] == "planning"  # unchanged


def test_approval_closes_the_session(store_and_workflow):
    store, workflow = store_and_workflow
    store.update_state(session={"hermes_session_id": "sess-1", "turns": 2})
    apply_approval(store, workflow, "APPROVE_PRD", approver="cli")
    assert store.read_state().get("session") is None
