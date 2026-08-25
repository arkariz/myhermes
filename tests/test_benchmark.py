"""The benchmark makes two claims -- pin them down as tests, not just a
report someone might stop rerunning.

  1. Our design sends fewer total tokens than an unmanaged Hermes-default
     over a multi-state project (the whole point of budgeting + boundary
     resets).
  2. The denylist is a structural guarantee: handed a denylisted artifact,
     the QA context package refuses to build rather than silently omitting
     it or including it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from benchmark.naive_baseline import run_naive
from benchmark.run_benchmark import demonstrate_denylist_gap, run_ours


def test_ours_sends_fewer_total_tokens_than_naive_default():
    with tempfile.TemporaryDirectory(prefix="agentic-dev-benchmark-test-") as tmp:
        ours = run_ours(Path(tmp) / "agent-state")
    naive = run_naive()

    total_ours = sum(o["prompt_tokens"] for o in ours)
    total_naive = sum(n.prompt_tokens for n in naive)

    assert len(ours) == len(naive)
    assert total_ours < total_naive


def test_boundary_turns_are_full_rebuilds_and_others_resume():
    with tempfile.TemporaryDirectory(prefix="agentic-dev-benchmark-test-") as tmp:
        ours = run_ours(Path(tmp) / "agent-state")

    kinds_by_index = {o["turn_index"]: o["kind"] for o in ours}
    assert kinds_by_index[1] == "full-rebuild"    # first turn, no session yet
    assert kinds_by_index[2] == "continuation"    # same state, resumes
    assert kinds_by_index[3] == "full-rebuild"    # after APPROVE_DISCOVERY
    assert kinds_by_index[6] == "full-rebuild"    # after APPROVE_PRD
    assert kinds_by_index[8] == "full-rebuild"    # after APPROVE_PRODUCT_DESIGN


def test_denylisted_artifact_refuses_to_build_for_qa():
    with tempfile.TemporaryDirectory(prefix="agentic-dev-benchmark-test-") as tmp:
        result = demonstrate_denylist_gap(Path(tmp) / "agent-state")

    assert "refused to build" in result["ours"]
    assert "tech-plan.md" in result["ours"]
