"""Human input semantics: free text is never approval; only an explicit
command or callback, matched exactly against the pending action, advances
a gate. Taken from brief S31 -- "looks good" and "yes" must never move a
project forward on their own."""

import pytest

from orchestrator.approvals import (
    ApprovalError,
    PendingAction,
    parse_decision_blocks,
    resolve_command,
    resolve_callback,
    resolve_free_text,
)

NOW = "2026-01-01T00:00:00+00:00"
PENDING_PRD = PendingAction(
    type="approval", approval_type="APPROVE_PRD", artifact="prd.md", artifact_revision=3
)


@pytest.mark.parametrize("text", ["looks good", "yes", "approve", "sounds great, ship it"])
def test_casual_text_is_always_feedback_never_approval(text):
    result = resolve_free_text(text, PENDING_PRD)
    assert result["kind"] == "feedback"


def test_exact_approve_command_with_matching_pending_action_approves():
    result = resolve_command(
        "/approve APPROVE_PRD", PENDING_PRD, approver="alice", source_turn="planning-05", now=NOW
    )
    assert result["kind"] == "approval"
    event = result["event"]
    assert event.approval_type == "APPROVE_PRD"
    assert event.artifact_revision == 3


def test_bare_approve_with_no_type_uses_the_pending_type():
    result = resolve_command("/approve", PENDING_PRD, approver="alice", source_turn=None, now=NOW)
    assert result["event"].approval_type == "APPROVE_PRD"


def test_approve_with_nothing_pending_is_rejected():
    with pytest.raises(ApprovalError, match="no approval is pending"):
        resolve_command("/approve", PendingAction.none(), approver="alice", source_turn=None, now=NOW)


def test_approve_with_wrong_type_is_rejected():
    with pytest.raises(ApprovalError, match="APPROVE_PRD"):
        resolve_command(
            "/approve APPROVE_MERGE", PENDING_PRD, approver="alice", source_turn=None, now=NOW
        )


def test_text_that_is_not_the_approve_command_falls_through_to_feedback():
    result = resolve_command("please approve this", PENDING_PRD, approver="a", source_turn=None, now=NOW)
    assert result["kind"] == "feedback"


def test_callback_with_matching_type_and_revision_approves():
    result = resolve_callback(
        "APPROVE_PRD", 3, PENDING_PRD, approver="alice", source_turn="planning-05", now=NOW
    )
    assert result["kind"] == "approval"


def test_callback_referencing_a_stale_artifact_revision_is_rejected():
    with pytest.raises(ApprovalError, match="stale artifact revision"):
        resolve_callback(
            "APPROVE_PRD", 2, PENDING_PRD, approver="alice", source_turn=None, now=NOW
        )


def test_callback_with_no_pending_action_is_rejected():
    with pytest.raises(ApprovalError):
        resolve_callback(
            "APPROVE_PRD", 3, PendingAction.none(), approver="alice", source_turn=None, now=NOW
        )


def test_callback_for_a_different_approval_type_is_rejected():
    with pytest.raises(ApprovalError, match="APPROVE_PRD"):
        resolve_callback(
            "APPROVE_MERGE", 3, PENDING_PRD, approver="alice", source_turn=None, now=NOW
        )


# ---- decision block parsing -------------------------------------------------


def test_decision_block_is_extracted_as_structured_data():
    output = '''Here is my reasoning.

```decision
id: 004-onboarding
decision: "Ask for child age, but make it optional"
tags: [onboarding]
```

Done.'''
    decisions = parse_decision_blocks(output)
    assert decisions == [
        {"id": "004-onboarding", "decision": "Ask for child age, but make it optional",
         "tags": ["onboarding"]}
    ]


def test_no_decision_block_returns_empty_list():
    assert parse_decision_blocks("Just some prose, no fences.") == []


def test_multiple_decision_blocks_are_all_extracted():
    output = "```decision\nid: a\n```\ntext\n```decision\nid: b\n```"
    decisions = parse_decision_blocks(output)
    assert [d["id"] for d in decisions] == ["a", "b"]


def test_malformed_yaml_in_decision_block_is_skipped_not_crashed():
    output = "```decision\n: this is not: valid: yaml: at all:\n```"
    # Should not raise -- either parses to something non-dict (dropped) or
    # yaml raises and the caller's test setup would catch it; here we assert
    # the function itself does not propagate a bare parser crash for a
    # merely-odd-but-parseable scalar.
    try:
        parse_decision_blocks(output)
    except Exception as exc:  # pragma: no cover - documents current behaviour
        pytest.fail(f"decision block parsing must not crash the turn: {exc}")
