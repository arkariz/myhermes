"""Integration tests for the full context-assembly pipeline: providers ->
denylist -> dedupe -> budget -> render. Also covers the two properties the
whole architecture depends on: a full rebuild is byte-identical given
identical inputs, and a denylist violation fails the turn rather than being
silently dropped.
"""

import pytest

from orchestrator.context.budget import ContextItem, ReadBudget
from orchestrator.context.builder import BuildRequest, ContextBuilder
from orchestrator.context.denylist import ContextPolicy, DenylistViolation
from orchestrator.context.tokens import TokenEstimator


class FixedProvider:
    """A provider that always returns the same fixed items -- lets tests
    control candidates precisely without touching the filesystem."""

    def __init__(self, name, items):
        self.name = name
        self._items = items

    def collect(self, request):
        return list(self._items)


def make_request(**overrides):
    defaults = dict(
        project_id="toy",
        role="planner",
        workflow_state="planning",
        task="Build a habit tracker for one user.",
        mode="assembled",
        kind="full",
        max_input_tokens=1000,
        source_revision="abc123",
        index_revision=None,
    )
    defaults.update(overrides)
    return BuildRequest(**defaults)


def test_assembled_prompt_embeds_content_and_ends_with_task():
    soul = ContextItem(key="souls/planner.md", layer=1, priority=1, volatility=0,
                        reason="role soul", content="You are the planner.")
    builder = ContextBuilder([FixedProvider("soul", [soul])], TokenEstimator())
    package = builder.build(make_request(), ContextPolicy(role="planner"))

    assert "You are the planner." in package.prompt
    assert package.prompt.strip().endswith("Build a habit tracker for one user.")
    assert package.mode == "assembled"
    assert package.manifest_keys == ["souls/planner.md"]


def test_guided_prompt_lists_paths_and_read_budget_instead_of_content():
    ref = ContextItem(key="lib/main.dart", layer=3, priority=5, volatility=4,
                       reason="entry point", content=None)
    read_budget = ReadBudget(max_files=5, max_bytes=1000, max_tool_calls=10)
    builder = ContextBuilder([FixedProvider("files", [ref])], TokenEstimator())
    request = make_request(role="builder", mode="guided", read_budget=read_budget)
    package = builder.build(request, ContextPolicy(role="builder"))

    assert "lib/main.dart" in package.prompt
    assert "Read budget" in package.prompt
    assert "at most 5 files" in package.prompt
    assert package.manifest[0].is_reference


def test_denylist_violation_fails_the_turn_not_silently_dropped():
    forbidden = ContextItem(key="artifacts/tech-plan.md", layer=2, priority=2,
                             volatility=2, reason="tech plan", content="secret plan")
    builder = ContextBuilder([FixedProvider("artifacts", [forbidden])], TokenEstimator())
    policy = ContextPolicy(role="qa", denylist=("artifacts/tech-plan.md",),
                            reason="QA must not see builder reasoning")

    with pytest.raises(DenylistViolation):
        builder.build(make_request(role="qa"), policy)


def test_lenient_mode_drops_denylisted_items_with_a_reason():
    forbidden = ContextItem(key="artifacts/tech-plan.md", layer=2, priority=2,
                             volatility=2, reason="tech plan", content="secret plan")
    builder = ContextBuilder(
        [FixedProvider("artifacts", [forbidden])], TokenEstimator(), strict_denylist=False
    )
    policy = ContextPolicy(role="qa", denylist=("artifacts/tech-plan.md",))
    package = builder.build(make_request(role="qa"), policy)

    assert package.manifest == []
    assert package.omitted[0][0] == "artifacts/tech-plan.md"


def test_budget_overflow_is_recorded_with_a_reason():
    huge = ContextItem(key="artifacts/prd.md", layer=2, priority=1, volatility=2,
                        reason="prd", content="word " * 5000)
    builder = ContextBuilder([FixedProvider("prd", [huge])], TokenEstimator())
    package = builder.build(
        make_request(max_input_tokens=10), ContextPolicy(role="planner")
    )
    assert package.manifest == []
    assert any("budget exceeded" in reason for _, reason in package.omitted)


def test_full_rebuild_is_byte_identical_given_identical_inputs():
    items = [
        ContextItem(key="souls/planner.md", layer=1, priority=1, volatility=0,
                    reason="soul", content="Soul text."),
        ContextItem(key="artifacts/prd.md", layer=2, priority=2, volatility=3,
                    reason="prd", content="Current PRD."),
    ]
    estimator = TokenEstimator()
    package_a = ContextBuilder([FixedProvider("p", items)], estimator).build(
        make_request(), ContextPolicy(role="planner")
    )
    package_b = ContextBuilder([FixedProvider("p", list(reversed(items)))], estimator).build(
        make_request(), ContextPolicy(role="planner")
    )
    assert package_a.prompt == package_b.prompt


def test_delta_package_carries_resumed_from():
    builder = ContextBuilder([FixedProvider("p", [])], TokenEstimator())
    request = make_request(kind="delta", resumed_from="planning-03")
    package = builder.build(request, ContextPolicy(role="planner"))
    assert package.kind == "delta"
    assert package.resumed_from == "planning-03"
    # A delta manifest is explicit that it doesn't describe the full input.
    assert "resumed" in package.to_yaml().lower()


def test_manifest_yaml_round_trips_key_fields():
    import yaml

    item = ContextItem(key="souls/qa.md", layer=1, priority=1, volatility=0,
                        reason="role soul", content="You are QA.")
    builder = ContextBuilder([FixedProvider("p", [item])], TokenEstimator())
    package = builder.build(make_request(role="qa"), ContextPolicy(role="qa"))
    doc = yaml.safe_load(package.to_yaml())

    assert doc["role"] == "qa"
    assert doc["included"][0]["key"] == "souls/qa.md"
    assert doc["source_revision"] == "abc123"
