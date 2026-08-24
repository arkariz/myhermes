"""The context builder -- the mandatory step between a workflow task and a
model invocation.

Pipeline:

    providers collect candidates
      -> enforce denylist            (correctness; may fail the turn)
      -> dedupe by content hash
      -> select by priority within budget
      -> order by volatility for rendering
      -> record every omission with a reason
      -> render + return a ContextPackage

Deterministic for a fixed (project revision, state, task, role, budget): the
same inputs must produce a byte-identical prompt, or neither prefix caching nor
reproducibility works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .budget import (
    ContextItem,
    ReadBudget,
    dedupe,
    order_for_render,
    select_within_budget,
)
from .denylist import ContextPolicy, DenylistViolation
from .manifest import ContextPackage
from .tokens import TokenEstimator


@dataclass(frozen=True)
class BuildRequest:
    project_id: str
    role: str
    workflow_state: str
    task: str
    mode: str                       # "assembled" | "guided"
    kind: str                       # "full" | "delta"
    max_input_tokens: int
    resumed_from: str | None = None
    since_turn: str | None = None   # delta mode: only what changed after this
    read_budget: ReadBudget | None = None
    source_revision: str | None = None
    index_revision: str | None = None


class ContextProvider(Protocol):
    """Contributes candidate items. Providers never decide what survives --
    they propose, the builder disposes. Phase 4 adds retrieval by adding a
    provider, not by editing this file."""

    name: str

    def collect(self, request: BuildRequest) -> Sequence[ContextItem]: ...


class ContextBuilder:
    def __init__(
        self,
        providers: Sequence[ContextProvider],
        estimator: TokenEstimator,
        *,
        strict_denylist: bool = True,
    ):
        self.providers = list(providers)
        self.estimator = estimator
        self.strict_denylist = strict_denylist

    def build(self, request: BuildRequest, policy: ContextPolicy) -> ContextPackage:
        candidates: list[ContextItem] = []
        for provider in self.providers:
            candidates.extend(provider.collect(request))

        # 1. Denylist first, before anything is counted or rendered.
        #    In strict mode a forbidden item aborts the turn rather than being
        #    quietly filtered, because a QA package that silently loses its
        #    blinding looks identical to one that never had it.
        omitted: list[tuple[str, str]] = []
        permitted: list[ContextItem] = []
        for item in candidates:
            if self.strict_denylist:
                policy.check(item.key)          # raises DenylistViolation
            if policy.permitted(item.key):
                permitted.append(item)
            else:
                omitted.append((item.key, f"excluded by {policy.role} context policy"))

        # 2. Count tokens once per item.
        for item in permitted:
            if item.content is not None:
                item.tokens = self.estimator.count(item.content)
            elif item.tokens == 0:
                # A reference costs roughly its path plus a line of framing.
                item.tokens = self.estimator.count(item.key) + 8

        # 3. Dedupe, then select within budget.
        deduped, dupes = dedupe(permitted)
        omitted.extend(dupes)

        result = select_within_budget(
            deduped, max_input_tokens=request.max_input_tokens
        )
        omitted.extend(result.omitted)

        # 4. Render stable-first so the prefix cache can hit.
        rendered_items = order_for_render(result.selected)
        prompt = self.render(request, rendered_items)

        return ContextPackage(
            prompt=prompt,
            mode=request.mode,
            kind=request.kind,
            role=request.role,
            workflow_state=request.workflow_state,
            manifest=rendered_items,
            omitted=omitted,
            estimated_tokens=result.total_tokens,
            resumed_from=request.resumed_from,
            read_budget=request.read_budget,
            source_revision=request.source_revision,
            index_revision=request.index_revision,
        )

    def render(self, request: BuildRequest, items: Sequence[ContextItem]) -> str:
        """Render the final prompt.

        Content items are embedded. Reference items are listed as paths for the
        agent to read itself -- Pang's approach, kept for code roles where
        pre-reading is a guess.
        """
        parts: list[str] = []
        references: list[ContextItem] = []

        for item in items:
            if item.is_reference:
                references.append(item)
                continue
            heading = item.section or item.key
            parts.append(f"## {heading}\n\n{item.content.strip()}\n")

        if references:
            lines = ["## Files and paths available to you", ""]
            lines += [f"- `{i.key}` — {i.reason}" for i in references]
            if request.read_budget:
                b = request.read_budget
                lines += [
                    "",
                    f"Read budget: at most {b.max_files} files, "
                    f"{b.max_bytes} bytes, {b.max_tool_calls} tool calls. "
                    "Read what you need and nothing more.",
                ]
            parts.append("\n".join(lines) + "\n")

        parts.append(f"## Your task\n\n{request.task.strip()}\n")
        return "\n".join(parts)


__all__ = [
    "BuildRequest",
    "ContextBuilder",
    "ContextProvider",
    "ContextPackage",
    "ContextPolicy",
    "DenylistViolation",
    "ReadBudget",
]
