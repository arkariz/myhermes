""""Hermes default" comparator -- what a project sees with no orchestrator.

Two things are true about running `hermes` directly, with no state machine,
no context builder, no session-boundary policy on top of it:

  1. There is nothing that ever *removes* content from a session. Every
     prior human message and every prior response either sits in the
     resumed session's own history (billed as input tokens on every later
     turn -- confirmed live in the Phase 1 spike: cache_read_tokens grows
     turn over turn on a resumed session, meaning the *input* it's a cache
     hit against also grows) or, if a caller manually re-sends history each
     time instead of using --resume, gets pasted in verbatim. Either way the
     total context a turn requires is monotonically non-decreasing, with no
     concept of "this is over budget, drop the lowest-priority parts."
  2. There is no role-based denylist. Nothing stops a later turn -- even one
     playing a QA/reviewer role via prompt instructions -- from having every
     prior artifact and every prior turn in its context, because there is no
     mechanism that would ever remove one.

This module simulates (1) directly and demonstrates (2) via
`demonstrate_denylist_gap()` in run_benchmark.py. Simulating (1) as an
explicit history dump (rather than driving a real `--resume` session) is a
deliberate, stated approximation: we don't have a way to bill a real
provider for "how many tokens would this session's growing history cost"
without actually running it turn by turn and paying for every prior turn
again, since a resumed session's *reported* input_tokens already reflects
whatever the provider does internally with its own history. The dump is the
same set of bytes a resumed session accumulates and is never able to drop --
it is not claimed to be byte-identical to what any specific provider's
internal session format would send.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_dev.domain.context.tokens import TokenEstimator

from .scenario import SCENARIO, Turn


@dataclass
class NaiveTurnResult:
    turn_index: int
    state: str
    prompt_tokens: int
    cumulative_history_items: int


def run_naive(estimator: TokenEstimator | None = None) -> list[NaiveTurnResult]:
    """Replay SCENARIO as an unmanaged, ever-growing conversation.

    No soul, no project identity, no budget, no denylist, no session reset
    on state change or approval -- exactly what's left when none of
    orchestrator/context/* exists and a caller just keeps talking to one
    Hermes session.
    """
    estimator = estimator or TokenEstimator()
    history: list[str] = []
    results: list[NaiveTurnResult] = []

    turn_index = 0
    for step in SCENARIO:
        if not isinstance(step, Turn):
            continue  # approvals are a no-op here: nothing resets, ever
        turn_index += 1
        prompt = "\n\n".join([*history, f"Human: {step.human}"])
        tokens = estimator.count(prompt)
        results.append(NaiveTurnResult(
            turn_index=turn_index, state=step.state,
            prompt_tokens=tokens, cumulative_history_items=len(history),
        ))
        history.append(f"Human: {step.human}")
        history.append(f"Agent: {step.response}")

    return results
