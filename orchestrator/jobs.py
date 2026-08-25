"""Turn execution -- the piece that actually runs one turn end to end.

Ties together everything built so far:

    state_machine   -- which role, which artifact, can this state resume?
    sessions        -- resume or rebuild, and why
    context/        -- assemble the prompt, enforce the denylist, budget it
    runtime.hermes  -- invoke Hermes with the verified two-path contract
    store           -- persist the turn, the artifact, the session
    events          -- record what happened and why, for every turn

A turn here always means one of: a fresh boundary turn, or a continuation
of a live session. Autonomous/gate states never continue (state_machine.
State.allows_continuation is False for them), so SessionManager.decide()
already encodes that rule -- jobs.py does not re-implement it.

Bounded retries only: `max_attempts` on the state, mirrored from Pang's
frontmatter counters. Exhausting attempts diverts to `blocked`, never loops
forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import artifact_versioning, summarizer
from .approvals import parse_decision_blocks
from .config import AgentsConfig, ModelsConfig
from .context.builder import BuildRequest, ContextBuilder
from .context.denylist import ContextPolicy
from .context.providers import (
    ArtifactSectionProvider,
    DecisionsProvider,
    ProjectIdentityProvider,
    RecentTurnsProvider,
    RoleSoulProvider,
    SummaryProvider,
)
from .context.tokens import TokenEstimator
from .events import EventLog, EventType
from .sessions import Session, SessionManager, SessionPolicy
from .state_machine import State, WorkflowDefinition
from .store import ProjectStore

try:
    from runtime.hermes import HermesRequest, HermesResult
    from runtime.hermes import run as hermes_run
    from runtime.client import RuntimeClientError
    from runtime.client import run as runtime_client_run
except ImportError:  # pragma: no cover - exercised only outside the repo root
    HermesRequest = HermesResult = hermes_run = None  # type: ignore[assignment]
    RuntimeClientError = None  # type: ignore[assignment]
    runtime_client_run = None  # type: ignore[assignment]


class TurnBlocked(Exception):
    """Raised when a turn's attempts are exhausted and the project is now
    blocked. The caller (CLI/Telegram handler) surfaces this to the human --
    it is never retried automatically past this point."""


@dataclass
class TurnOutcome:
    turn_id: str
    response: str
    session: Session
    workflow_state: str
    failed: bool
    next_workflow_state: str | None = None  # set only when this turn's
                                             # completion also advanced state


class TurnRunner:
    """Executes one turn for one project.

    One instance per project per call is fine -- everything expensive
    (souls, decisions, artifacts) is read lazily by providers inside
    `collect()`, not at construction.
    """

    def __init__(
        self,
        *,
        project_id: str,
        store: ProjectStore,
        workflow: WorkflowDefinition,
        agents: AgentsConfig,
        models: ModelsConfig,
        souls_dir: str = "souls",
        estimator: TokenEstimator | None = None,
        session_policy: SessionPolicy | None = None,
        runtime_url: str | None = None,
    ):
        self.project_id = project_id
        self.store = store
        self.workflow = workflow
        self.agents = agents
        self.models = models
        self.souls_dir = souls_dir
        self.estimator = estimator or TokenEstimator()
        self.session_policy = session_policy or SessionPolicy()
        self.session_manager = SessionManager(self.session_policy)
        self.events = EventLog(store, project_id)
        # None (the default) means "call runtime.hermes.run() in-process" --
        # how every project has run so far, on a single host with no
        # container networking. Set this (e.g. from AGENTIC_RUNTIME_URL) to
        # route the same call through runtime/server.py over HTTP instead,
        # for the real orchestrator/agent-runtime container split.
        self.runtime_url = runtime_url

    # ---- turn id / bookkeeping -------------------------------------------

    def _next_turn_id(self, workflow_state: str) -> str:
        seq = self.store.next_turn_sequence(workflow_state)
        return f"{workflow_state}-{seq:02d}"

    def _load_session(self, state_data: dict) -> Session | None:
        return Session.from_dict(state_data.get("session"))

    # ---- the turn ----------------------------------------------------

    def run_turn(self, human_message: str) -> TurnOutcome:
        state_data = self.store.read_state()
        workflow_state_name = state_data.get("workflow_state", self.workflow.initial)
        state: State = self.workflow.get(workflow_state_name)

        if not state.runs_agent:
            raise TurnBlocked(
                f"state {workflow_state_name!r} does not run an agent "
                f"(kind={state.kind.value}); an approval event is needed instead"
            )

        role = state.role
        assert role is not None  # guaranteed by State validation for agent states
        role_cfg = self.agents.get(role)
        route = self.models.get(role)

        attempts = int(state_data.get("attempts", 0))
        if attempts >= state.max_attempts and state.kind.value == "autonomous":
            self.store.update_state(
                workflow_state="blocked",
                blocked_reason=f"max_attempts ({state.max_attempts}) exceeded in {workflow_state_name}",
            )
            self.events.emit(EventType.PROJECT_BLOCKED, workflow_state=workflow_state_name)
            raise TurnBlocked(f"{workflow_state_name}: max attempts exceeded")

        turn_id = self._next_turn_id(workflow_state_name)
        self.events.emit(
            EventType.AGENT_TURN_STARTED, workflow_state=workflow_state_name, turn_id=turn_id,
        )
        self.store.append_conversation_turn(workflow_state_name, "human", human_message)

        existing_session = self._load_session(state_data)
        decision = self.session_manager.decide(
            session=existing_session,
            state=state,
            role=role,
            artifact_revision=int(state_data.get("artifact_revision", 0)),
            decision_revision=self._decision_revision(),
            index_revision=state_data.get("index_revision"),
        )

        package = self._build_context(
            role=role, role_cfg=role_cfg, state=state,
            workflow_state_name=workflow_state_name, task=human_message,
            decision=decision, turn_id=turn_id,
        )
        self.store.write_turn_artifact(turn_id, "prompt.md", package.prompt)
        self.store.write_turn_artifact(turn_id, "manifest.yaml", package.to_yaml())
        self.events.context_built(
            workflow_state=workflow_state_name, turn_id=turn_id, role=role,
            mode=package.mode, kind=package.kind,
            estimated_tokens=package.estimated_tokens,
            manifest_keys=package.manifest_keys, omitted=package.omitted,
            source_revision=package.source_revision,
            index_revision=package.index_revision,
            model=route.model, provider=route.provider,
        )

        result = self._invoke_hermes(
            package_prompt=package.prompt, route=route, role_cfg=role_cfg,
            decision=decision, turn_id=turn_id,
        )

        self.store.write_turn_artifact(turn_id, "response.md", result.response)
        self.store.append_conversation_turn(workflow_state_name, "agent", result.response)
        self.events.turn_completed(
            workflow_state=workflow_state_name, turn_id=turn_id,
            session_id=result.session_id, usage=result.usage,
            estimated_tokens=package.estimated_tokens,
        )

        self._record_decisions(result.response)
        session = self._update_session(
            existing_session=existing_session, decision=decision, state=state,
            role=role, result=result, turn_id=turn_id, state_data=state_data,
        )

        if result.failed:
            self.events.emit(
                EventType.AGENT_TURN_FAILED, workflow_state=workflow_state_name, turn_id=turn_id,
            )
            self.store.update_state(attempts=attempts + 1)
            return TurnOutcome(
                turn_id=turn_id, response=result.response, session=session,
                workflow_state=workflow_state_name, failed=True,
            )

        if "summarizer" in self.models.routes:
            self._update_summary(workflow_state_name, turn_id, human_message, result.response)

        self._version_artifacts(workflow_state_name, turn_id)

        self.store.update_state(attempts=0)
        return TurnOutcome(
            turn_id=turn_id, response=result.response, session=session,
            workflow_state=workflow_state_name, failed=False,
        )

    # ---- helpers -----------------------------------------------------

    def _decision_revision(self) -> int:
        d = self.store.decisions_dir()
        if not d.exists():
            return 0
        return len(list(d.glob("*.md")))

    def _build_context(self, *, role, role_cfg, state, workflow_state_name, task, decision, turn_id):
        providers = [
            RoleSoulProvider(self.souls_dir),
            ProjectIdentityProvider(self.store, self.project_id),
            ArtifactSectionProvider(self.store),
            DecisionsProvider(self.store),
            SummaryProvider(self.store),
            RecentTurnsProvider(self.store, count=3),
        ]
        builder = ContextBuilder(providers, self.estimator)
        policy = ContextPolicy(
            role=role, allowlist=role_cfg.allowlist, denylist=role_cfg.denylist,
            reason=role_cfg.denylist_reason,
        )
        request = BuildRequest(
            project_id=self.project_id, role=role, workflow_state=workflow_state_name,
            task=task, mode=role_cfg.context_mode, kind=decision.kind,
            max_input_tokens=role_cfg.budget.max_input_tokens,
            resumed_from=decision.session_id if decision.resume else None,
            read_budget=role_cfg.read_budget,
            artifact_name=state.artifact,
        )
        return builder.build(request, policy)

    def _invoke_hermes(self, *, package_prompt, route, role_cfg, decision, turn_id):
        request = HermesRequest(
            prompt=package_prompt,
            home_dir=self.store.hermes_home(),
            provider=route.provider,
            model=route.model,
            toolsets=",".join(role_cfg.toolsets) if role_cfg.toolsets else None,
            resume_session_id=decision.session_id if decision.resume else None,
        )
        usage_file = self.store.turn_dir(turn_id) / "usage.json" if not decision.resume else None

        if self.runtime_url:
            if runtime_client_run is None:
                raise RuntimeError(
                    "runtime.client is not importable -- run from the repo root "
                    "or add it to sys.path"
                )
            try:
                return runtime_client_run(request, usage_file=usage_file, base_url=self.runtime_url)
            except RuntimeClientError as exc:
                raise RuntimeError(f"runtime server call failed: {exc}") from exc

        if hermes_run is None:
            raise RuntimeError(
                "runtime.hermes is not importable -- run from the repo root "
                "or add it to sys.path"
            )
        return hermes_run(request, usage_file=usage_file)

    def _record_decisions(self, response: str) -> None:
        for block in parse_decision_blocks(response):
            decision_id = str(block.get("id", "unrecorded"))
            path = self.store.decisions_dir() / f"{decision_id}.md"
            body = "\n".join(f"{k}: {v}" for k, v in block.items())
            self.store.decisions_dir().mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            self.events.emit(EventType.DECISION_RECORDED, payload={"id": decision_id})

    def _update_summary(self, workflow_state: str, turn_id: str, human_message: str, agent_response: str) -> None:
        """Best-effort: a failed summarization call is logged as itself
        failing, never as this turn failing -- the turn already succeeded
        by the time this runs."""
        route = self.models.get("summarizer")
        result = summarizer.update_summary(
            self.store, workflow_state, route,
            human_message=human_message, agent_response=agent_response,
        )
        if not result.failed:
            self.events.emit(
                EventType.SUMMARY_UPDATED, workflow_state=workflow_state, turn_id=turn_id,
            )

    def _version_artifacts(self, workflow_state: str, turn_id: str) -> None:
        """Snapshot artifacts/ into git if anything changed this turn.

        Doesn't care how the change got there -- an agent's own file-write
        tool today, a human editing by hand, or a future explicit
        "extract artifact from response" step all look identical here:
        whatever's on disk gets committed if it differs from last time.
        """
        commit_hash = artifact_versioning.commit(
            self.store.artifacts_dir(), message=f"{workflow_state} / {turn_id}",
        )
        if commit_hash is not None:
            self.events.emit(
                EventType.ARTIFACT_UPDATED, workflow_state=workflow_state, turn_id=turn_id,
                payload={"commit": commit_hash},
            )

    def _update_session(self, *, existing_session, decision, state, role, result, turn_id, state_data):
        if decision.resume and existing_session is not None:
            session = self.session_manager.record_turn(existing_session, failed=result.failed)
        else:
            if existing_session is not None:
                self.events.emit(
                    EventType.SESSION_INVALIDATED,
                    payload={"reason": decision.reason.value if decision.reason else None},
                )
            session = self.session_manager.open(
                hermes_session_id=result.session_id or "unknown",
                role=role, state=state.name, turn_id=turn_id,
                artifact_revision=int(state_data.get("artifact_revision", 0)),
                decision_revision=self._decision_revision(),
                index_revision=state_data.get("index_revision"),
            )
            self.events.emit(
                EventType.SESSION_OPENED, session_id=session.hermes_session_id,
            )
        self.store.update_state(session=session.to_dict())
        return session
