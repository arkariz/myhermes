"""Telegram handlers -- MVP set: /link, /status, plain-text turns, and one
inline-button approval flow.

Strictly async (brief S24): `handle_message`, the only handler that could
trigger a multi-minute agent turn, does nothing but enqueue a `TurnJob` and
return. The turn itself runs in `inbox_worker`, a single background task
started once at bot startup, off the event loop that is also serving every
other chat's updates. A handler holding that loop hostage for the length of
a Hermes call would mean no other project could get a response, and no
other command in the *same* chat could either -- Pang solved the equivalent
problem with `start_new_session=True` process detachment; an asyncio queue
plus a worker task is the same idea inside one process.

`inbox_worker` processes one job at a time, globally, across every project.
That's stricter than required -- ProjectStore's own contract only demands
serialization *per project* (state.yaml is read-modify-write, not
lock-protected: "two writers to one project is a bug, not a case to merge")
-- but a single global queue is the simplest thing that cannot violate that
requirement, and Phase 2's traffic (one human, a handful of projects) does
not need per-project concurrency to feel responsive.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from indexing.dart_adapter import DartAnalyzerIndexer
from indexing.remote import RemoteIndexer
from orchestrator.approval_flow import apply_approval
from orchestrator.approvals import ApprovalError
from orchestrator.config import AgentsConfig, ModelsConfig
from orchestrator.jobs import TurnBlocked, TurnRunner
from orchestrator.registry import ProjectNotFound
from orchestrator.state_machine import StateKind, WorkflowDefinition, WorkflowError
from orchestrator.store import ProjectStore

from .routing import RoutingError, link_project, resolve_project
from .topics import ForumTopicError, create_forum_topic

logger = logging.getLogger(__name__)

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _default_host_root() -> Path:
    """Where a Telegram-created project's source tree defaults to, when the
    human gave only a name and not a --host-path (the CLI always requires
    one; /create can't ask a chat message for a filesystem path without it
    feeling like a second form to fill out, so it picks a default instead).
    Override via TELEGRAM_DEFAULT_HOST_ROOT for your own machine's layout --
    there is no universal correct default. Read at call time, not import
    time, so a test (or a restart after changing the env var) sees it."""
    return Path(os.environ.get("TELEGRAM_DEFAULT_HOST_ROOT", str(Path.home() / "agentic-dev-projects")))


@dataclass(frozen=True)
class TurnJob:
    project_id: str
    chat_id: int
    thread_id: int | None
    human_message: str


@dataclass(frozen=True)
class CallbackPayload:
    """What an "Approve" button remembers about the message it was attached
    to. `artifact_revision` is the load-bearing field: it is what lets
    apply_approval() detect a stale click (S31's own acceptance criterion)."""

    project_id: str
    approval_type: str
    artifact_revision: int


# ---- shared loaders (same bot_data dict, whether called from a handler
# via context.bot_data or from the worker via app.bot_data -- ptb's
# Application.bot_data *is* that dict, not a copy of it) ------------------


def _config_dir(bot_data: dict) -> Path:
    return bot_data["config_dir"]


def _store_and_workflow(bot_data: dict, project_id: str) -> tuple[ProjectStore, WorkflowDefinition]:
    entry = bot_data["registry"].get(project_id)
    store = ProjectStore(entry.state_path)
    workflow = WorkflowDefinition.load(_config_dir(bot_data) / "workflow.yaml")
    return store, workflow


def _build_runner(bot_data: dict, project_id: str) -> TurnRunner:
    entry = bot_data["registry"].get(project_id)
    store, workflow = _store_and_workflow(bot_data, project_id)
    config_dir = _config_dir(bot_data)
    agents = AgentsConfig.load(config_dir / "agents.yaml")
    models = ModelsConfig.load(config_dir / "models.yaml")
    runtime_url = os.environ.get("AGENTIC_RUNTIME_URL")
    return TurnRunner(
        project_id=project_id, store=store, workflow=workflow,
        agents=agents, models=models, souls_dir=str(bot_data["souls_dir"]),
        runtime_url=runtime_url,
        # Same "always wired, never required" reasoning as orchestrator/cli.py,
        # including RemoteIndexer over the same runtime_url when it's set --
        # the orchestrator container has no Dart SDK on purpose.
        project_source_root=entry.host_path,
        indexer=RemoteIndexer(base_url=runtime_url) if runtime_url else DartAnalyzerIndexer(),
    )


# ---- commands -------------------------------------------------------------


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /link <project_id>")
        return

    project_id = context.args[0]
    registry = context.bot_data["registry"]
    thread_id = update.message.message_thread_id
    try:
        entry = link_project(
            registry, project_id,
            chat_id=update.effective_chat.id, thread_id=thread_id,
        )
    except ProjectNotFound:
        await update.message.reply_text(f"No such project: {project_id!r}")
        return

    await update.message.reply_text(
        f"Linked this chat/topic to {entry.name!r}. "
        "Messages sent here now drive its turns."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registry = context.bot_data["registry"]
    try:
        entry = resolve_project(
            registry, chat_id=update.effective_chat.id,
            thread_id=update.message.message_thread_id,
        )
    except RoutingError as exc:
        await update.message.reply_text(str(exc))
        return

    store = ProjectStore(entry.state_path)
    state_data = store.read_state()
    session = state_data.get("session")
    lines = [
        f"project: {entry.name}",
        f"workflow_state: {state_data.get('workflow_state', '(none)')}",
        f"attempts: {state_data.get('attempts', 0)}",
        (
            f"session: {session.get('hermes_session_id')} ({session.get('turns')} turns)"
            if session else "session: (none)"
        ),
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/create -- the counterpart to /link for a project that doesn't exist
    yet. Two-step, not `/create <name>` in one shot: asking in a follow-up
    message reads more naturally as a form ("what's it called?") than a
    command argument would, and it's the same shape /link already isn't --
    /link takes an id inline because the project already exists and the id
    is just a lookup key; here the name is being chosen, not looked up.
    """
    key = (update.effective_chat.id, update.message.message_thread_id)
    context.bot_data["pending_create"][key] = True
    await update.message.reply_text(
        "What should the new project be called? "
        "(letters, numbers, hyphens, underscores)"
    )


async def _create_project_from_chat(
    update: Update, context: ContextTypes.DEFAULT_TYPE, name: str,
) -> None:
    registry = context.bot_data["registry"]
    config_dir = _config_dir(context.bot_data)

    if not _PROJECT_NAME_RE.match(name):
        await update.message.reply_text(
            "Project names can only contain letters, numbers, hyphens, and "
            "underscores. Run /create again to retry."
        )
        return

    try:
        registry.get(name)
    except ProjectNotFound:
        pass
    else:
        await update.message.reply_text(f"Project {name!r} already exists.")
        return

    host_path = _default_host_root() / name
    state_path = host_path.parent / ".agentic-dev" / name
    registry.register(name, host_path=str(host_path), state_path=str(state_path))

    store = ProjectStore(str(state_path))
    store.ensure_layout()
    workflow = WorkflowDefinition.load(config_dir / "workflow.yaml")
    store.write_state({"workflow_state": workflow.initial, "attempts": 0})

    # Only auto-clear a leading gate (config/workflow.yaml's real `backlog`
    # state, guarding with START_PROJECT) -- not hardcoded to that name, so
    # a workflow whose initial state runs an agent directly isn't force-fed
    # an approval type it never declared.
    initial_state = workflow.get(workflow.initial)
    if initial_state.kind is StateKind.GATE:
        apply_approval(
            store, workflow, initial_state.approval_type,
            approver=f"telegram:{update.effective_user.id}",
        )

    await update.message.reply_text(
        f"Created {name!r} at {host_path} -- now at state "
        f"{store.read_state()['workflow_state']!r}."
    )

    chat_id = update.effective_chat.id
    try:
        thread_id = create_forum_topic(context.bot.token, chat_id, name)
    except ForumTopicError as exc:
        await update.message.reply_text(
            f"Project created, but couldn't create a forum topic ({exc}). "
            f"Use /link {name} in a chat/topic to drive it manually instead."
        )
        return

    registry.link_telegram(name, chat_id=chat_id, thread_id=thread_id)
    await context.bot.send_message(
        chat_id=chat_id, message_thread_id=thread_id,
        text=f"This topic now drives project {name!r}. Send a message to start.",
    )


# ---- plain-text turns -------------------------------------------------------


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = (update.effective_chat.id, update.message.message_thread_id)
    pending_create = context.bot_data["pending_create"]
    if key in pending_create:
        del pending_create[key]
        await _create_project_from_chat(update, context, update.message.text.strip())
        return

    registry = context.bot_data["registry"]
    thread_id = update.message.message_thread_id
    try:
        entry = resolve_project(registry, chat_id=update.effective_chat.id, thread_id=thread_id)
    except RoutingError as exc:
        await update.message.reply_text(str(exc))
        return

    job = TurnJob(
        project_id=entry.name, chat_id=update.effective_chat.id,
        thread_id=thread_id, human_message=update.message.text,
    )
    await context.bot_data["inbox"].put(job)
    await update.message.reply_text("Working on it...")


async def inbox_worker(app: Application) -> None:
    queue = app.bot_data["inbox"]
    while True:
        job: TurnJob = await queue.get()
        try:
            await _process_turn_job(app, job)
        except Exception:
            logger.exception("turn job failed: %r", job)
            try:
                await app.bot.send_message(
                    chat_id=job.chat_id, message_thread_id=job.thread_id,
                    text="Something went wrong running that turn -- check the logs.",
                )
            except Exception:
                logger.exception("also failed to report the failure to chat %s", job.chat_id)
        finally:
            queue.task_done()


async def _process_turn_job(app: Application, job: TurnJob) -> None:
    runner = _build_runner(app.bot_data, job.project_id)

    try:
        outcome = await asyncio.to_thread(runner.run_turn, job.human_message)
    except TurnBlocked as exc:
        await app.bot.send_message(
            chat_id=job.chat_id, message_thread_id=job.thread_id,
            text=f"Blocked: {exc}",
        )
        return

    state_data = runner.store.read_state()
    state = runner.workflow.get(state_data.get("workflow_state", runner.workflow.initial))

    # Only offer Approve on a turn that actually succeeded -- state.requires_approval
    # reflects the CURRENT workflow state (e.g. "discovery" is still an
    # approval-gated state regardless of whether this particular turn
    # produced anything), not whether this turn's output is worth approving.
    # A failed turn leaving the state unchanged still passed that check, so
    # the button showed up next to an empty/error response -- clicking it
    # would advance the workflow on top of nothing. Found live: exactly
    # this, after a turn failed on a rate-limited free-tier model.
    reply_markup = None
    if not outcome.failed and state.requires_approval and state.runs_agent:
        token = secrets.token_hex(4)
        app.bot_data["callback_table"][token] = CallbackPayload(
            project_id=job.project_id, approval_type=state.approval_type,
            artifact_revision=state_data.get("artifact_revision", 0),
        )
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"Approve ({state.approval_type})", callback_data=token),
        ]])

    if outcome.failed:
        reason = outcome.failure_reason or "no reason reported"
        text = f"[turn failed] {reason}"
    else:
        text = outcome.response

    await app.bot.send_message(
        chat_id=job.chat_id, message_thread_id=job.thread_id,
        text=text, reply_markup=reply_markup,
    )


# ---- approval callback ------------------------------------------------------


async def handle_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # stop Telegram's own loading spinner immediately

    payload: CallbackPayload | None = context.bot_data["callback_table"].pop(query.data, None)
    await query.edit_message_reply_markup(reply_markup=None)  # can't be double-clicked either way

    if payload is None:
        await query.message.reply_text(
            "This approval button has expired -- use /status, then /approve "
            "if you still need to act on it."
        )
        return

    store, workflow = _store_and_workflow(context.bot_data, payload.project_id)
    approver = f"telegram:{update.effective_user.id}"
    try:
        next_state = apply_approval(
            store, workflow, payload.approval_type, approver=approver,
            callback_artifact_revision=payload.artifact_revision,
        )
    except (ApprovalError, WorkflowError) as exc:
        await query.message.reply_text(f"Not approved: {exc}")
        return

    name = update.effective_user.first_name or approver
    await query.message.reply_text(f"Approved by {name}. Moved to `{next_state}`.")
