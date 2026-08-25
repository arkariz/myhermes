"""Benchmark: our hybrid context/session design vs. a naive Hermes-default.

    python -m benchmark.run_benchmark

Both sides replay the exact same scenario (benchmark/scenario.py): the same
human messages, the same canned agent responses. `hermes_run` is
monkeypatched on our side too -- this benchmark measures what our own real
code (state machine, session manager, context builder, denylist) decides to
send, not the model's behavior. That is the thing actually being claimed:
"the orchestration layer reduces and bounds context, and structurally
enforces the denylist" -- not "gpt-4o-mini is cheap". A --live flag that
drives real Hermes calls for a cost/cache_read_tokens sanity check is a
natural follow-up, deliberately not built here: it would need real spend to
say anything a mock can't already show about what gets SENT, which is the
part under our control.

What's measured:

  1. Per-turn and total prompt tokens, ours vs. naive, using the same
     TokenEstimator for both so the comparison isn't an artifact of counting
     differently.
  2. Which of our turns were continuations (resumed session, delta-sized
     input) vs. full boundary rebuilds -- naive has no such distinction,
     every turn is "more of the same growing thing."
  3. A structural correctness check: handed a denylisted artifact as a
     candidate, does the QA context package refuse to build? Naive has no
     mechanism that could even ask this question.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Bootstrapping sys.path so `python benchmark/run_benchmark.py` (a direct
# script invocation, not `-m`) can find the orchestrator/runtime/settings
# top-level modules -- this is the one legitimate use of Path(__file__)
# in this codebase: it locates the CODE (where to import from), never the
# CONFIG (where workflow.yaml/agents.yaml live, which is settings.py's
# job below). `python -m benchmark.run_benchmark` from the repo root
# doesn't need this at all (the interpreter already puts cwd on
# sys.path), but the insert is harmless and keeps the script runnable
# either way.
_CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CODE_ROOT))

import orchestrator.jobs as jobs_module  # noqa: E402
import orchestrator.summarizer as summarizer_module  # noqa: E402
from orchestrator.approvals import PendingAction, resolve_command  # noqa: E402
from orchestrator.config import AgentsConfig, ModelsConfig  # noqa: E402
from orchestrator.context.builder import BuildRequest, ContextBuilder  # noqa: E402
from orchestrator.context.denylist import ContextPolicy, DenylistViolation  # noqa: E402
from orchestrator.context.providers import ArtifactSectionProvider  # noqa: E402
from orchestrator.context.tokens import TokenEstimator  # noqa: E402
from orchestrator.jobs import TurnRunner  # noqa: E402
from orchestrator.state_machine import WorkflowDefinition  # noqa: E402
from orchestrator.store import ProjectStore  # noqa: E402
from runtime.hermes import HermesResult  # noqa: E402

from benchmark.naive_baseline import run_naive  # noqa: E402
from benchmark.scenario import SCENARIO, Approval, Turn  # noqa: E402
from settings import settings  # noqa: E402

CONFIG_DIR = settings.config_dir
SOULS_DIR = settings.souls_dir


def _approve(store: ProjectStore, workflow: WorkflowDefinition, approval_type: str) -> str:
    state_data = store.read_state()
    current = state_data.get("workflow_state", workflow.initial)
    state = workflow.get(current)
    pending = PendingAction(
        type="approval" if state.requires_approval else "none",
        approval_type=state.approval_type,
        artifact=state.artifact,
        artifact_revision=state_data.get("artifact_revision", 0),
    )
    resolve_command(
        f"/approve {approval_type}", pending, approver="benchmark",
        source_turn=None, now=datetime.now(timezone.utc).isoformat(),
    )
    next_state = workflow.advance(current, approval_type=approval_type)
    store.update_state(
        workflow_state=next_state, attempts=0,
        artifact_revision=state_data.get("artifact_revision", 0) + 1,
        session=None,
    )
    return next_state


def run_ours(state_root: Path) -> list[dict]:
    store = ProjectStore(state_root)
    store.ensure_layout()
    workflow = WorkflowDefinition.load(CONFIG_DIR / "workflow.yaml")
    agents = AgentsConfig.load(CONFIG_DIR / "agents.yaml")
    models = ModelsConfig.load(CONFIG_DIR / "models.yaml")
    store.write_state({"workflow_state": workflow.initial, "attempts": 0})

    _approve(store, workflow, "START_PROJECT")  # backlog -> discovery

    runner = TurnRunner(
        project_id="benchmark", store=store, workflow=workflow,
        agents=agents, models=models, souls_dir=str(SOULS_DIR),
    )
    estimator = runner.estimator

    # config/models.yaml routes a real "summarizer" role -- TurnRunner calls
    # it after every successful turn (orchestrator/summarizer.py). Mock it
    # the same way as the main hermes_run below, or this benchmark would try
    # to launch a real (and here, absent) hermes binary purely as a side
    # effect of using the real config.
    def fake_summary(request, usage_file=None):
        return HermesResult(
            response="(benchmark summary placeholder)",
            usage={"failed": False}, exit_code=0, session_id=None,
        )
    summarizer_module.hermes_run = fake_summary

    responses = iter(step for step in SCENARIO if isinstance(step, Turn))
    results: list[dict] = []
    turn_index = 0

    for step in SCENARIO:
        if isinstance(step, Approval):
            _approve(store, workflow, step.approval_type)
            continue

        turn_index += 1
        canned = next(responses)
        assert canned.human == step.human  # scenario order sanity check

        session_before = store.read_state().get("session")
        was_resumed = session_before is not None

        def fake_run(request, usage_file=None, _resp=canned.response):
            return HermesResult(
                response=_resp,
                usage={"failed": False, "session_id": "bench-session"},
                exit_code=0,
                session_id="bench-session",
            )

        jobs_module.hermes_run = fake_run
        outcome = runner.run_turn(step.human)

        prompt = store.read_turn_artifact(outcome.turn_id, "prompt.md") or ""
        results.append({
            "turn_index": turn_index,
            "turn_id": outcome.turn_id,
            "state": step.state,
            "kind": "continuation" if was_resumed else "full-rebuild",
            "prompt_tokens": estimator.count(prompt),
        })

    return results


def demonstrate_denylist_gap(state_root: Path) -> dict:
    """QA is handed a candidate `tech-plan.md` item. Ours must refuse to
    build the package; naive has no mechanism that could refuse anything."""
    store = ProjectStore(state_root)
    store.artifact("tech-plan.md").parent.mkdir(parents=True, exist_ok=True)
    store.artifact("tech-plan.md").write_text(
        "# Tech plan\n\nUse a monorepo, Riverpod for state, and a custom "
        "sync engine the builder decided on mid-implementation.",
        encoding="utf-8",
    )

    agents = AgentsConfig.load(CONFIG_DIR / "agents.yaml")
    qa_cfg = agents.get("qa")
    policy = ContextPolicy(
        role="qa", allowlist=qa_cfg.allowlist,
        denylist=qa_cfg.denylist, reason=qa_cfg.denylist_reason,
    )
    builder = ContextBuilder([ArtifactSectionProvider(store)], TokenEstimator())
    request = BuildRequest(
        project_id="benchmark", role="qa", workflow_state="qa",
        task="Verify the implementation against acceptance criteria.",
        mode="guided", kind="full", max_input_tokens=12000,
        artifact_name="tech-plan.md",
    )

    try:
        builder.build(request, policy)
        ours_outcome = "BUG: package built anyway (should never happen)"
    except DenylistViolation as exc:
        ours_outcome = f"refused to build: {exc}"

    naive_outcome = (
        "included verbatim -- a naive prompt has no policy object to consult, "
        "so QA would read the builder's own reasoning about *how* it was "
        "built instead of verifying *what* it built against the acceptance "
        "criteria"
    )
    return {"ours": ours_outcome, "naive": naive_outcome}


def render_report(ours: list[dict], naive: list, denylist_demo: dict) -> str:
    lines = ["# Context/token benchmark", ""]
    n_turns = len(ours)
    n_states = len(dict.fromkeys(o["state"] for o in ours))
    lines.append(
        f"Same {n_turns}-turn scenario across {n_states} workflow states "
        "(discovery -> planning -> product-design -> architecture, each "
        "gated by an approval), replayed against our hybrid context/session "
        "design and against a naive Hermes-default with no orchestration "
        "layer. `hermes_run` is mocked on both sides -- what's measured is "
        "what each design actually assembles and sends, not model behavior."
    )
    lines.append("")
    lines.append("| # | State | Ours: turn kind | Ours tokens | Naive tokens | Naive / Ours |")
    lines.append("|---|---|---|---|---|---|")

    total_ours = 0
    total_naive = 0
    for o, n in zip(ours, naive):
        total_ours += o["prompt_tokens"]
        total_naive += n.prompt_tokens
        ratio = n.prompt_tokens / o["prompt_tokens"] if o["prompt_tokens"] else float("inf")
        lines.append(
            f"| {o['turn_index']} | {o['state']} | {o['kind']} | "
            f"{o['prompt_tokens']} | {n.prompt_tokens} | {ratio:.2f}x |"
        )

    lines.append("")
    reduction = (1 - total_ours / total_naive) * 100 if total_naive else 0.0
    lines.append(f"**Total tokens -- ours: {total_ours}, naive: {total_naive} "
                  f"({reduction:.1f}% reduction).**")
    lines.append("")
    boundary_turns = [o["turn_index"] for o in ours if o["kind"] == "full-rebuild"]
    lines.append(
        "Naive's total grows monotonically because nothing is ever removed "
        "from its history and there is no budget to enforce it against; "
        f"ours resets to a small, budgeted rebuild at each approval boundary "
        f"(turns {', '.join(str(t) for t in boundary_turns)}) and only ever "
        "resends the delta on continuation turns. The gap widens with every "
        "state the project passes through -- by the last state the naive "
        "prompt is carrying the full discovery-through-product-design "
        "transcript into a turn that only needs the architecture role's own "
        "context."
    )
    lines.append("")
    lines.append("## Denylist correctness (not a token metric)")
    lines.append("")
    lines.append(f"- **Ours:** {denylist_demo['ours']}")
    lines.append(f"- **Naive:** {denylist_demo['naive']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentic-dev-benchmark-") as tmp:
        state_root = Path(tmp) / "agent-state"
        ours = run_ours(state_root)
        naive = run_naive()
        denylist_demo = demonstrate_denylist_gap(state_root)

    report = render_report(ours, naive, denylist_demo)
    out_path = REPO_ROOT / "benchmark" / "report.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
