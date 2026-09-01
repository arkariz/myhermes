"""Denylist is a correctness invariant. It fails the turn -- it does not warn."""

import pytest

from agentic_dev.domain.context.denylist import ContextPolicy, DenylistViolation


def test_qa_cannot_receive_tech_plan():
    policy = ContextPolicy(
        role="qa",
        denylist=("artifacts/tech-plan.md", "task-log.md", "conversations/implementation/"),
        reason="QA must verify against acceptance criteria, not builder reasoning",
    )
    with pytest.raises(DenylistViolation, match="qa"):
        policy.check("artifacts/tech-plan.md")


def test_qa_can_receive_acceptance_criteria():
    policy = ContextPolicy(
        role="qa",
        allowlist=("artifacts/prd.md#acceptance-criteria", "git-diff", "test-output"),
        denylist=("artifacts/tech-plan.md",),
    )
    policy.check("artifacts/prd.md#acceptance-criteria")  # must not raise
    assert policy.permitted("artifacts/prd.md#acceptance-criteria") is True


def test_qa_allowlist_excludes_anything_not_declared():
    # An allowlist narrows inclusion even to items that aren't denied.
    policy = ContextPolicy(
        role="qa",
        allowlist=("artifacts/prd.md#acceptance-criteria",),
    )
    assert policy.permitted("artifacts/prd.md#full-spec") is False


def test_directory_denylist_covers_every_turn_inside_it():
    policy = ContextPolicy(role="reviewer", denylist=("conversations/implementation/",))
    with pytest.raises(DenylistViolation):
        policy.check("conversations/implementation/003-agent.md")


def test_denying_an_artifact_covers_its_sections():
    policy = ContextPolicy(role="qa", denylist=("artifacts/tech-plan.md",))
    with pytest.raises(DenylistViolation):
        policy.check("artifacts/tech-plan.md#data-flow")


def test_role_with_no_policy_permits_everything():
    policy = ContextPolicy(role="planner")
    policy.check("artifacts/prd.md")  # no denylist -> no violation
    assert policy.permitted("anything") is True


def test_filter_reports_kept_and_dropped_with_reasons():
    policy = ContextPolicy(role="qa", denylist=("artifacts/tech-plan.md",))
    kept, dropped = policy.filter(["artifacts/prd.md", "artifacts/tech-plan.md"])
    assert kept == ["artifacts/prd.md"]
    assert dropped[0][0] == "artifacts/tech-plan.md"
    assert "qa" in dropped[0][1]
