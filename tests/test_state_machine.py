"""The state machine must be boring and total: no dangling edges, no way for
an LLM to move a project, no unbounded retry."""

from pathlib import Path

import pytest

from orchestrator.state_machine import (
    StateKind,
    WorkflowDefinition,
    WorkflowError,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "workflow.yaml"


@pytest.fixture(scope="module")
def wf() -> WorkflowDefinition:
    return WorkflowDefinition.load(CONFIG)


def test_shipped_config_loads_and_validates(wf):
    # Validation runs in __init__; reaching here means every `next`,
    # `on_failure` and `on_reject` target exists and every state is reachable.
    assert wf.initial == "backlog"
    assert "implementation" in wf.states


def test_every_non_terminal_state_has_a_successor(wf):
    for state in wf.states.values():
        if state.kind is not StateKind.TERMINAL:
            assert state.next, f"{state.name} has no next"


def test_agent_states_declare_a_role(wf):
    for state in wf.states.values():
        if state.runs_agent:
            assert state.role, f"{state.name} runs an agent without a role"


def test_only_collaborative_states_allow_continuation(wf):
    for state in wf.states.values():
        expected = state.kind is StateKind.COLLABORATIVE
        assert state.allows_continuation is expected, state.name


def test_gate_refuses_to_advance_without_approval(wf):
    with pytest.raises(WorkflowError, match="requires approval"):
        wf.advance("awaiting-approval")


def test_gate_refuses_the_wrong_approval_type(wf):
    with pytest.raises(WorkflowError, match="requires approval"):
        wf.advance("awaiting-approval", approval_type="APPROVE_PRD")


def test_gate_advances_on_the_exact_approval_type(wf):
    assert wf.advance("awaiting-approval", approval_type="APPROVE_TECH_PLAN") == "implementation"


def test_collaborative_state_also_needs_its_typed_approval(wf):
    # A collaborative state ending an agent invocation is NOT completion.
    with pytest.raises(WorkflowError):
        wf.advance("planning")
    assert wf.advance("planning", approval_type="APPROVE_PRD") == "product-design"


def test_autonomous_state_advances_without_approval(wf):
    assert wf.advance("implementation") == "review"


def test_terminal_state_cannot_advance(wf):
    with pytest.raises(WorkflowError, match="terminal"):
        wf.advance("done")


def test_retry_stays_in_state_until_attempts_are_spent(wf):
    assert wf.get("implementation").max_attempts == 2
    assert wf.fail("implementation", attempts=1) == "implementation"


def test_retry_diverts_to_blocked_once_exhausted(wf):
    # The bound that makes an infinite autonomous loop impossible.
    assert wf.fail("implementation", attempts=2) == "blocked"
    assert wf.fail("implementation", attempts=99) == "blocked"


def test_unknown_state_is_rejected(wf):
    with pytest.raises(WorkflowError, match="unknown state"):
        wf.get("not-a-state")


# ---- definition validation -------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "wf.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_dangling_next_is_rejected(tmp_path):
    path = _write(tmp_path, """
initial: a
states:
  a: {kind: autonomous, role: builder, next: nowhere}
""")
    with pytest.raises(WorkflowError, match="not defined"):
        WorkflowDefinition.load(path)


def test_gate_without_approval_type_is_rejected(tmp_path):
    path = _write(tmp_path, """
initial: a
states:
  a: {kind: gate, next: b}
  b: {kind: terminal}
""")
    with pytest.raises(WorkflowError, match="approval_type"):
        WorkflowDefinition.load(path)


def test_unreachable_state_is_rejected(tmp_path):
    path = _write(tmp_path, """
initial: a
states:
  a: {kind: autonomous, role: builder, next: b}
  b: {kind: terminal}
  orphan: {kind: terminal}
""")
    with pytest.raises(WorkflowError, match="unreachable"):
        WorkflowDefinition.load(path)
