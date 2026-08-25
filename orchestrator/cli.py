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
import sys
from datetime import datetime, timezone
from pathlib import Path

from .approvals import ApprovalError, PendingAction, resolve_command
from .config import AgentsConfig, ModelsConfig
from .jobs import TurnBlocked, TurnRunner
from .registry import ProjectRegistry
from .state_machine import WorkflowDefinition
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
    return 0


def _build_runner(project_id: str) -> tuple[TurnRunner, ProjectStore]:
    entry = _registry().get(project_id)
    store = ProjectStore(entry.state_path)
    runner = TurnRunner(
        project_id=project_id, store=store, workflow=_load_workflow(),
        agents=_load_agents(), models=_load_models(),
        souls_dir=str(CONFIG_DIR / "souls"),
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
    state_data = store.read_state()
    current = state_data.get("workflow_state", workflow.initial)
    state = workflow.get(current)

    pending = PendingAction(
        type="approval" if state.requires_approval else "none",
        approval_type=state.approval_type,
        artifact=state.artifact,
        artifact_revision=state_data.get("artifact_revision", 0),
    )
    try:
        result = resolve_command(
            f"/approve {args.approval_type}", pending, approver="cli",
            source_turn=None, now=datetime.now(timezone.utc).isoformat(),
        )
    except ApprovalError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    if result["kind"] != "approval":
        print("Not an approval.", file=sys.stderr)
        return 1

    next_state = workflow.advance(current, approval_type=args.approval_type)
    store.update_state(
        workflow_state=next_state,
        attempts=0,
        artifact_revision=state_data.get("artifact_revision", 0) + 1,
        session=None,  # force a full rebuild in the new state
    )
    store.append_event({
        "type": "APPROVAL_GRANTED",
        "project_id": args.project_id,
        "workflow_state": current,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {"approval_type": args.approval_type, "next_state": next_state},
    })
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
