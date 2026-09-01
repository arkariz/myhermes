"""Incremental summarization (brief S17.3).

`summarize(previous_summary + new_turn)` as its own cheap one-shot Hermes
call -- not a general-purpose helper, one narrow job: fold one new
human/agent turn into a running summary, preserving decisions, open
questions, constraints, rejected alternatives, artifact status, and next
actions.

Summaries matter most at boundaries: a resumed session already carries its
own transcript, so the win here is specifically for what a *fresh* session
sees. SESSION_INVALIDATED means the next turn's context rebuild has nothing
but the artifact and whatever raw conversation happens to still be within
RecentTurnsProvider's window -- this is what carries the thread across that
gap instead. Run every turn regardless (continuation or boundary) rather
than only at detected boundaries: it's one cheap call on a cheap model
(config/models.yaml routes it to deepseek-v3), and conditioning it on
SessionManager's internal boundary decision would couple two things that
don't need to know about each other.

Best-effort and optional: a project with no `summarizer` route configured
in models.yaml simply never calls this (see jobs.py's TurnRunner), and a
failed summarization call leaves the previous summary on disk untouched
rather than corrupting it with an empty or partial rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ports.agent_runtime import AgentRuntime, HermesRequest

from ..domain.roles import ModelRoute
from ..adapters.storage.store import ProjectStore


_PROMPT_TEMPLATE = """You maintain a running summary of one project's workflow state, for another AI agent that will read only this summary, never the full conversation.

Previous summary:
{previous_summary}

New turn:
Human: {human_message}
Agent: {agent_response}

Rewrite the summary to fold in this turn. Preserve, across every rewrite:
- Decisions made, and why
- Open questions still unresolved
- Constraints and non-goals
- Alternatives that were considered and rejected
- Current status of the artifact being produced
- Next actions

Be concise -- compress the turn into what a future turn actually needs to
know, don't restate it verbatim. Output only the updated summary, no
preamble or commentary."""


def build_prompt(previous_summary: str, human_message: str, agent_response: str) -> str:
    return _PROMPT_TEMPLATE.format(
        previous_summary=previous_summary.strip() or "(none yet -- this is the first turn)",
        human_message=human_message.strip(),
        agent_response=agent_response.strip(),
    )


@dataclass
class SummarizeResult:
    summary: str
    failed: bool


def summarize(
    *,
    previous_summary: str,
    human_message: str,
    agent_response: str,
    home_dir,
    provider: str,
    model: str,
    agent_runtime: AgentRuntime,
) -> SummarizeResult:
    """One cheap one-shot Hermes call -- never resumes a session. A summary
    call has no business inheriting another session's context, and its own
    output is the only thing that needs to persist afterward.

    `agent_runtime` is the same object `jobs.py::TurnRunner` used for the
    turn itself -- an `InProcessHermesRuntime` or an `HttpAgentRuntime`,
    picked once at the entrypoint. Needed for real, not hypothetical: the
    orchestrator container has no Hermes CLI at all (deliberately; see
    docker/orchestrator/Dockerfile), and `config/models.yaml` configures a
    `summarizer` route by default, so every successful turn would
    otherwise crash here the first time this ran inside that container --
    this module used to pick its own routing independently of jobs.py,
    which is exactly how that bug went unnoticed.
    """
    prompt = build_prompt(previous_summary, human_message, agent_response)
    request = HermesRequest(prompt=prompt, home_dir=home_dir, provider=provider, model=model)

    try:
        result = agent_runtime.run(request)
    except RuntimeError:
        # Best-effort per this module's contract: a failed summary call
        # leaves the previous summary untouched, it never fails the turn.
        # (HttpAgentRuntime raises RuntimeError for a transport failure;
        # InProcessHermesRuntime's own launch failures are NOT caught here,
        # unchanged from before -- those propagate, same as jobs.py's own
        # main-turn call never caught them either.)
        return SummarizeResult(summary=previous_summary, failed=True)

    if result.failed:
        return SummarizeResult(summary=previous_summary, failed=True)
    return SummarizeResult(summary=result.response.strip(), failed=False)


def update_summary(
    store: ProjectStore,
    workflow_state: str,
    route: ModelRoute,
    *,
    human_message: str,
    agent_response: str,
    agent_runtime: AgentRuntime,
) -> SummarizeResult:
    """Read the running summary for `workflow_state`, fold in one new turn,
    and persist the result if the call succeeded."""
    previous = store.read_summary(workflow_state) or ""
    result = summarize(
        previous_summary=previous, human_message=human_message, agent_response=agent_response,
        home_dir=store.hermes_home(), provider=route.provider, model=route.model,
        agent_runtime=agent_runtime,
    )
    if not result.failed:
        store.write_summary(workflow_state, result.summary)
    return result
