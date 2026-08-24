"""Deterministic workflow state machine.

Transitions come from config/workflow.yaml and are validated when loaded, so a
malformed graph fails at startup rather than halfway through a project.

The invariant this file exists to protect: an LLM never decides what happens
next. Agents produce content; the engine and typed human approvals decide the
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class StateKind(str, Enum):
    AUTONOMOUS = "autonomous"
    COLLABORATIVE = "collaborative"
    GATE = "gate"
    TERMINAL = "terminal"


class WorkflowError(Exception):
    """Raised for an invalid workflow definition or an illegal transition."""


@dataclass(frozen=True)
class State:
    name: str
    kind: StateKind
    role: str | None = None
    artifact: str | None = None
    completion: str | None = None
    approval_type: str | None = None
    next: str | None = None
    on_failure: str | None = None
    on_reject: str | None = None
    max_attempts: int = 1
    recoverable: bool = False

    @property
    def runs_agent(self) -> bool:
        return self.kind in (StateKind.AUTONOMOUS, StateKind.COLLABORATIVE)

    @property
    def allows_continuation(self) -> bool:
        """Only collaborative states may resume a Hermes session.

        Autonomous turns always rebuild: they are decision points that must
        carry a complete, reproducible manifest.
        """
        return self.kind is StateKind.COLLABORATIVE

    @property
    def requires_approval(self) -> bool:
        return self.kind is StateKind.GATE or self.completion == "approval"


class WorkflowDefinition:
    """The loaded, validated state graph."""

    def __init__(self, initial: str, states: dict[str, State]):
        self.initial = initial
        self.states = states
        self._validate()

    @classmethod
    def load(cls, path: Path | str) -> "WorkflowDefinition":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        initial = raw.get("initial")
        if not initial:
            raise WorkflowError("workflow.yaml must define `initial`")

        states: dict[str, State] = {}
        for name, spec in (raw.get("states") or {}).items():
            spec = spec or {}
            try:
                kind = StateKind(spec.get("kind", ""))
            except ValueError:
                raise WorkflowError(
                    f"state {name!r}: unknown kind {spec.get('kind')!r}"
                ) from None
            states[name] = State(
                name=name,
                kind=kind,
                role=spec.get("role"),
                artifact=spec.get("artifact"),
                completion=spec.get("completion"),
                approval_type=spec.get("approval_type"),
                next=spec.get("next"),
                on_failure=spec.get("on_failure"),
                on_reject=spec.get("on_reject"),
                max_attempts=int(spec.get("max_attempts", 1)),
                recoverable=bool(spec.get("recoverable", False)),
            )
        return cls(initial, states)

    def _validate(self) -> None:
        if self.initial not in self.states:
            raise WorkflowError(f"initial state {self.initial!r} is not defined")

        for state in self.states.values():
            # Every referenced target must exist. A dangling `next` is the
            # failure mode that would otherwise strand a project mid-workflow.
            for attr in ("next", "on_failure", "on_reject"):
                target = getattr(state, attr)
                if target is not None and target not in self.states:
                    raise WorkflowError(
                        f"state {state.name!r}.{attr} -> {target!r} is not defined"
                    )

            if state.kind is StateKind.TERMINAL:
                continue

            if state.next is None:
                raise WorkflowError(f"non-terminal state {state.name!r} has no `next`")

            if state.runs_agent and not state.role:
                raise WorkflowError(f"state {state.name!r} runs an agent but has no `role`")

            if state.kind is StateKind.GATE and not state.approval_type:
                raise WorkflowError(f"gate {state.name!r} has no `approval_type`")

            if state.completion == "approval" and not state.approval_type:
                raise WorkflowError(
                    f"state {state.name!r} completes by approval but has no `approval_type`"
                )

        self._assert_reachable()

    def _assert_reachable(self) -> None:
        """Every state must be reachable from `initial`.

        An unreachable state is dead config -- usually a rename that missed a
        reference, which is far easier to catch here than in production.
        """
        seen: set[str] = set()
        frontier = [self.initial]
        while frontier:
            name = frontier.pop()
            if name in seen:
                continue
            seen.add(name)
            state = self.states[name]
            for target in (state.next, state.on_failure, state.on_reject):
                if target:
                    frontier.append(target)

        orphans = set(self.states) - seen
        # Exceptional states are entered programmatically, not via an edge.
        orphans -= {"cancelled", "failed", "blocked"}
        if orphans:
            raise WorkflowError(f"unreachable states: {sorted(orphans)}")

    # ---- queries ---------------------------------------------------------

    def get(self, name: str) -> State:
        if name not in self.states:
            raise WorkflowError(f"unknown state {name!r}")
        return self.states[name]

    def can_transition(self, frm: str, to: str) -> bool:
        state = self.get(frm)
        return to in {
            t for t in (state.next, state.on_failure, state.on_reject) if t
        } or to in {"cancelled", "failed", "blocked"}

    def advance(self, frm: str, *, approval_type: str | None = None) -> str:
        """Return the state that follows `frm` on the happy path.

        A gate or approval-completed state refuses to move without the exact
        approval type it declared. This is where "looks good" is prevented from
        becoming an approval -- the caller must present a typed event.
        """
        state = self.get(frm)
        if state.kind is StateKind.TERMINAL:
            raise WorkflowError(f"state {frm!r} is terminal")

        if state.requires_approval:
            if approval_type is None:
                raise WorkflowError(
                    f"state {frm!r} requires approval {state.approval_type!r}; none given"
                )
            if approval_type != state.approval_type:
                raise WorkflowError(
                    f"state {frm!r} requires approval {state.approval_type!r}, "
                    f"got {approval_type!r}"
                )

        assert state.next is not None  # guaranteed by _validate
        return state.next

    def fail(self, frm: str, attempts: int) -> str:
        """Where a failed autonomous turn goes.

        Retries stay in the same state until max_attempts is spent, then divert
        to on_failure. Bounded by construction -- there is no path that loops
        forever without human involvement.
        """
        state = self.get(frm)
        if attempts < state.max_attempts:
            return frm
        return state.on_failure or "blocked"
