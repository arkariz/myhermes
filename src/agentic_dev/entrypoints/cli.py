"""CLI driver -- no Telegram, no Docker orchestration.

    agentic init-workspace ../agentic-workspace   # one-time, per machine
    agentic project new <id> --host-path P [--platform X]
    agentic turn <id> "<message>"
    agentic approve <id> <APPROVAL_TYPE>
    agentic status <id>

This is intentionally the thinnest possible layer over turn_runner.TurnRunner
and workflow.WorkflowDefinition -- Telegram drives the exact same
TurnRunner, just from a different entry point.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.indexing.dart import DartAnalyzerIndexer
from ..adapters.indexing.remote import RemoteIndexer
from ..adapters.hermes.runtime import HttpAgentRuntime, InProcessHermesRuntime
from ..settings import Settings, WorkspaceNotFound, settings
from ..adapters.telegram.topics import ForumTopicError, create_forum_topic
from ..adapters.workspace import WorkspaceAlreadyExists, init_workspace

from ..app.approval_flow import apply_approval, default_continuation_message
from ..app.project_creation import IMPORT_PROJECT_APPROVAL, create_project
from ..domain.approvals import ApprovalError
from ..domain.roles import AgentsConfig, ModelsConfig
from ..app.turn_runner import TurnBlocked, TurnRunner
from ..adapters.registry import ProjectRegistry
from ..domain.workflow import WorkflowDefinition, WorkflowError
from ..adapters.storage.store import ProjectStore


def _load_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.load(settings.workflow_file)


def _load_agents() -> AgentsConfig:
    return AgentsConfig.load(settings.agents_file)


def _load_models() -> ModelsConfig:
    return ModelsConfig.load(settings.models_file, env=os.environ)


def _registry() -> ProjectRegistry:
    return ProjectRegistry(settings.projects_file)


def cmd_project_new(args: argparse.Namespace) -> int:
    reg = _registry()
    state_path = args.state_path or str(Path(args.host_path).parent / ".agentic-dev" / args.name)
    workflow = _load_workflow()
    entry, store = create_project(
        reg, name=args.name, host_path=args.host_path, state_path=state_path,
        workflow=workflow, platform=args.platform,
    )
    store.append_event({
        "type": "PROJECT_CREATED",
        "project_id": args.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {"host_path": entry.host_path, "state_path": entry.state_path},
    })
    print(f"Created project {args.name!r} at state {workflow.initial!r}")
    print(f"  host_path:  {entry.host_path}")
    print(f"  state_path: {entry.state_path}")

    _maybe_create_telegram_topic(reg, args.name)

    if args.import_existing:
        # A real typed approval, not a raw state.yaml write -- fires the
        # same IMPORT_PROJECT event a human would via `/approve
        # IMPORT_PROJECT`, then auto-continues into onboarding's first
        # turn (the auditor role, see config/workflow.yaml's `onboarding`
        # state) exactly like any other approval does.
        return _approve_and_continue(args.name, store, workflow, IMPORT_PROJECT_APPROVAL)

    return 0


def _maybe_create_telegram_topic(reg: ProjectRegistry, project_id: str) -> None:
    """Auto-create and link a Telegram forum topic for this project.

    Optional by design: with no home group configured, project creation
    behaves exactly as before (manual `/link` in Telegram still works). This
    never fails project creation -- a Telegram-side error here is reported
    and swallowed, not raised, because the project itself was already
    created successfully by the time this runs.
    """
    token = settings.telegram_bot_token
    chat_id = settings.telegram_forum_chat_id
    if not token or not chat_id:
        return

    try:
        thread_id = create_forum_topic(token, chat_id, project_id)
    except ForumTopicError as exc:
        print(f"  telegram: could not create a forum topic ({exc})", file=sys.stderr)
        return

    reg.link_telegram(project_id, chat_id=chat_id, thread_id=thread_id)
    print(f"  telegram: created topic {project_id!r} (thread {thread_id}) and linked it")


def _build_runner(project_id: str) -> tuple[TurnRunner, ProjectStore]:
    entry = _registry().get(project_id)
    store = ProjectStore(entry.state_path)
    runtime_url = settings.runtime_url
    # Unset (the default) means InProcessHermesRuntime -- same single-host
    # behavior as every live run so far. Set AGENTIC_RUNTIME_URL to route
    # Hermes invocations through runtime/server.py over HTTP instead --
    # picked once, here, not re-decided per call.
    agent_runtime = HttpAgentRuntime(base_url=runtime_url) if runtime_url else InProcessHermesRuntime()
    runner = TurnRunner(
        project_id=project_id, store=store, workflow=_load_workflow(),
        agents=_load_agents(), models=_load_models(),
        souls_dir=str(settings.souls_dir),
        agent_runtime=agent_runtime,
        # Always wired, never a hard requirement: both indexers'
        # supports() returns False for a project with no pubspec.yaml, and
        # TurnRunner._current_index_revision() degrades to "no index" on
        # any indexer failure -- a project that isn't Dart, or a host/
        # container with no Dart SDK, behaves exactly as it did before
        # Phase 4. RemoteIndexer over the same AGENTIC_RUNTIME_URL when
        # it's set: the orchestrator container has no Dart SDK on purpose
        # (the toolchain lives only in agent-runtime), so DartAnalyzerIndexer
        # would fail to launch there -- see runtime/server.py's POST /index.
        project_source_root=entry.host_path,
        indexer=RemoteIndexer(base_url=runtime_url) if runtime_url else DartAnalyzerIndexer(),
    )
    return runner, store


def _print_turn_outcome(outcome) -> int:
    print(f"[{outcome.turn_id}] ({'FAILED' if outcome.failed else 'ok'})")
    print(outcome.response)
    if outcome.failed and outcome.failure_reason:
        print(f"reason: {outcome.failure_reason}", file=sys.stderr)
    return 1 if outcome.failed else 0


def cmd_turn(args: argparse.Namespace) -> int:
    runner, store = _build_runner(args.project_id)
    try:
        outcome = runner.run_turn(args.message)
    except TurnBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1

    return _print_turn_outcome(outcome)


def _approve_and_continue(
    project_id: str, store: ProjectStore, workflow: WorkflowDefinition, approval_type: str,
) -> int:
    """Apply one approval, then auto-continue into the newly-unlocked
    state so approving doesn't need a manual follow-up message just to
    kick off work the approval already authorized -- see
    approval_flow.default_continuation_message. Shared by `cmd_approve`
    and `cmd_project_new`'s `--import` (which fires IMPORT_PROJECT the
    same way a human typing `/approve IMPORT_PROJECT` would, not by
    writing state.yaml directly)."""
    current = store.read_state().get("workflow_state", workflow.initial)
    try:
        next_state = apply_approval(store, workflow, approval_type, approver="cli")
    except (ApprovalError, WorkflowError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"Approved. {current!r} -> {next_state!r}")

    next_state_obj = workflow.get(next_state)
    if next_state_obj.runs_agent:
        runner, _ = _build_runner(project_id)
        message = default_continuation_message(approval_type, next_state)
        try:
            outcome = runner.run_turn(message)
        except TurnBlocked as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 0  # the approval itself still succeeded
        return _print_turn_outcome(outcome)

    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    entry = _registry().get(args.project_id)
    store = ProjectStore(entry.state_path)
    workflow = _load_workflow()
    return _approve_and_continue(args.project_id, store, workflow, args.approval_type)


def cmd_init_workspace(args: argparse.Namespace) -> int:
    if args.check:
        # A container health-check: does settings resolution succeed at
        # all, against whatever AGENTIC_WORKSPACE/sibling-repo is already
        # in place? No path argument, nothing is created.
        resolved = Settings.from_env()
        if resolved.workspace is None:
            print(f"NOT FOUND: {WorkspaceNotFound()}", file=sys.stderr)
            return 1
        print(f"workspace: {resolved.workspace}")
        return 0

    if not args.path:
        print("a target path is required unless --check is given", file=sys.stderr)
        return 1

    try:
        created = init_workspace(Path(args.path))
    except WorkspaceAlreadyExists as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print(f"Workspace ready at {created}")
    print(f"  config/:      {created / 'config'}")
    print(f"  agent-state/: {created / 'agent-state'}")
    print(f"  projects/:    {created / 'projects'}")
    print("Set AGENTIC_WORKSPACE to this path, or place it as a sibling")
    print("'../agentic-workspace' of this code repo so settings.py finds it")
    print("without any env var.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    entry = _registry().get(args.project_id)
    store = ProjectStore(entry.state_path)
    state_data = store.read_state()
    print(f"project:        {args.project_id}")
    print(f"workflow_state: {state_data.get('workflow_state', '(none)')}")
    print(f"attempts:       {state_data.get('attempts', 0)}")
    session = state_data.get("session")
    if session:
        print(f"session:        {session.get('hermes_session_id')} ({session.get('turns')} turns)")
    else:
        print("session:        (none)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic")
    sub = parser.add_subparsers(dest="command", required=True)

    init_ws = sub.add_parser("init-workspace")
    init_ws.add_argument("path", nargs="?", default=None)
    init_ws.add_argument(
        "--check", action="store_true",
        help="verify settings resolution succeeds against the current environment; creates nothing",
    )
    init_ws.set_defaults(func=cmd_init_workspace)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    new = project_sub.add_parser("new")
    new.add_argument("name")
    new.add_argument("--host-path", required=True)
    new.add_argument("--state-path", default=None)
    new.add_argument("--platform", default="flutter")
    new.add_argument(
        "--import", dest="import_existing", action="store_true",
        help="the project at --host-path already exists (any origin, documented or not) "
             "-- route to onboarding for an audit instead of starting at discovery",
    )
    new.set_defaults(func=cmd_project_new)

    turn = sub.add_parser("turn")
    turn.add_argument("project_id")
    turn.add_argument("message")
    turn.set_defaults(func=cmd_turn)

    approve = sub.add_parser("approve")
    approve.add_argument("project_id")
    approve.add_argument("approval_type")
    approve.set_defaults(func=cmd_approve)

    status = sub.add_parser("status")
    status.add_argument("project_id")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
