"""CLI driver for the Phase 1 demo -- no Telegram, no Docker orchestration.

    python -m orchestrator.cli project new <id> --host-path P [--platform X]
    python -m orchestrator.cli turn <id> "<message>"
    python -m orchestrator.cli approve <id> <APPROVAL_TYPE>
    python -m orchestrator.cli status <id>

This is intentionally the thinnest possible layer over jobs.TurnRunner and
state_machine.WorkflowDefinition -- Telegram (Phase 2) will drive the exact
same TurnRunner, just from a different entry point.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from telegram_bot.topics import ForumTopicError, create_forum_topic

from .approval_flow import apply_approval
from .approvals import ApprovalError
from .config import AgentsConfig, ModelsConfig
from .jobs import TurnBlocked, TurnRunner
from .registry import ProjectRegistry
from .state_machine import WorkflowDefinition, WorkflowError
from .store import ProjectStore

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def _load_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.load(CONFIG_DIR / "workflow.yaml")


def _load_agents() -> AgentsConfig:
    return AgentsConfig.load(CONFIG_DIR / "agents.yaml")


def _load_models() -> ModelsConfig:
    return ModelsConfig.load(CONFIG_DIR / "models.yaml")


def _registry() -> ProjectRegistry:
    return ProjectRegistry(CONFIG_DIR / "projects.yaml")


def cmd_project_new(args: argparse.Namespace) -> int:
    reg = _registry()
    state_path = args.state_path or str(Path(args.host_path).parent / ".agentic-dev" / args.name)
    entry = reg.register(
        args.name, host_path=args.host_path, state_path=state_path, platform=args.platform,
    )
    store = ProjectStore(entry.state_path)
    store.ensure_layout()
    workflow = _load_workflow()
    store.write_state({"workflow_state": workflow.initial, "attempts": 0})
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
    return 0


def _maybe_create_telegram_topic(reg: ProjectRegistry, project_id: str) -> None:
    """Auto-create and link a Telegram forum topic for this project.

    Optional by design: with no home group configured, project creation
    behaves exactly as before (manual `/link` in Telegram still works). This
    never fails project creation -- a Telegram-side error here is reported
    and swallowed, not raised, because the project itself was already
    created successfully by the time this runs.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FORUM_CHAT_ID")
    if not token or not chat_id:
        return

    try:
        thread_id = create_forum_topic(token, int(chat_id), project_id)
    except ForumTopicError as exc:
        print(f"  telegram: could not create a forum topic ({exc})", file=sys.stderr)
        return

    reg.link_telegram(project_id, chat_id=int(chat_id), thread_id=thread_id)
    print(f"  telegram: created topic {project_id!r} (thread {thread_id}) and linked it")


def _build_runner(project_id: str) -> tuple[TurnRunner, ProjectStore]:
    entry = _registry().get(project_id)
    store = ProjectStore(entry.state_path)
    runner = TurnRunner(
        project_id=project_id, store=store, workflow=_load_workflow(),
        agents=_load_agents(), models=_load_models(),
        souls_dir=str(CONFIG_DIR / "souls"),
        # Unset (the default) means runtime.hermes.run() in-process, same
        # single-host behavior as every live run so far. Set this to route
        # Hermes invocations through runtime/server.py over HTTP instead.
        runtime_url=os.environ.get("AGENTIC_RUNTIME_URL"),
    )
    return runner, store


def cmd_turn(args: argparse.Namespace) -> int:
    runner, store = _build_runner(args.project_id)
    try:
        outcome = runner.run_turn(args.message)
    except TurnBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1

    print(f"[{outcome.turn_id}] ({'FAILED' if outcome.failed else 'ok'})")
    print(outcome.response)
    return 1 if outcome.failed else 0


def cmd_approve(args: argparse.Namespace) -> int:
    entry = _registry().get(args.project_id)
    store = ProjectStore(entry.state_path)
    workflow = _load_workflow()
    current = store.read_state().get("workflow_state", workflow.initial)

    try:
        next_state = apply_approval(
            store, workflow, args.approval_type, approver="cli",
        )
    except (ApprovalError, WorkflowError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"Approved. {current!r} -> {next_state!r}")
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
    parser = argparse.ArgumentParser(prog="orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    new = project_sub.add_parser("new")
    new.add_argument("name")
    new.add_argument("--host-path", required=True)
    new.add_argument("--state-path", default=None)
    new.add_argument("--platform", default="flutter")
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
