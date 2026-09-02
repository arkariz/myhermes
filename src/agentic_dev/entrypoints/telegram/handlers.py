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

from ...adapters.git.project import InvalidRepositoryUrl, ProjectGitError, clone, repo_name_from_url
from ...adapters.indexing.dart import DartAnalyzerIndexer
from ...adapters.indexing.remote import RemoteIndexer
from ...app.approval_flow import apply_approval, default_continuation_message
from ...app.project_creation import IMPORT_PROJECT_APPROVAL, create_project, leading_gate_approval_type
from ...domain.approvals import ApprovalError
from ...domain.roles import AgentsConfig, ModelsConfig
from ...app.turn_runner import TurnBlocked, TurnRunner
from ...adapters.registry import ProjectNotFound
from ...domain.workflow import StateKind, WorkflowDefinition, WorkflowError
from ...adapters.storage.store import ProjectStore
from ...adapters.hermes.runtime import HttpAgentRuntime, InProcessHermesRuntime
from ...settings import settings

from .routing import RoutingError, link_project, resolve_project
from ...adapters.telegram.topics import ForumTopicError, create_forum_topic

logger = logging.getLogger(__name__)

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

# Loose on purpose -- this only has to FIND a github.com URL inside a
# human's free-text message ("here's my repo: https://github.com/a/b, can
# you take a look"), not validate it. The real, strict check is
# adapters/git/project.py::GITHUB_HTTPS_RE, applied to whatever this
# extracts before it's ever used for anything.
_GITHUB_URL_SEARCH_RE = re.compile(r"https://github\.com/\S+")


def _default_host_root() -> Path:
    """Where a Telegram-created project's source tree defaults to, when the
    human gave only a name and not a --host-path (the CLI always requires
    one; /create can't ask a chat message for a filesystem path without it
    feeling like a second form to fill out, so it picks a default instead).
    Override via TELEGRAM_DEFAULT_HOST_ROOT for your own machine's layout --
    there is no universal correct default."""
    return settings.telegram_default_host_root


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
    models = ModelsConfig.load(config_dir / "models.yaml", env=os.environ)
    runtime_url = settings.runtime_url
    agent_runtime = HttpAgentRuntime(base_url=runtime_url) if runtime_url else InProcessHermesRuntime()
    return TurnRunner(
        project_id=project_id, store=store, workflow=workflow,
        agents=agents, models=models, souls_dir=str(bot_data["souls_dir"]),
        agent_runtime=agent_runtime,
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
    workflow = WorkflowDefinition.load(config_dir / "workflow.yaml")
    _entry, store = create_project(
        registry, name=name, host_path=str(host_path), state_path=str(state_path),
        workflow=workflow,
    )

    # Only auto-clear a leading gate (config/workflow.yaml's real `backlog`
    # state) on the conventional "start a new project" approval -- not
    # hardcoded to a single fixed approval_type, since backlog now offers
    # two (START_PROJECT and IMPORT_PROJECT, see State.next_by_approval);
    # leading_gate_approval_type() picks START_PROJECT specifically for
    # THIS (plain /create) flow, or returns None for a workflow whose
    # leading gate doesn't use either convention -- never guesses wrong.
    initial_state = workflow.get(workflow.initial)
    if initial_state.kind is StateKind.GATE:
        approval_type = leading_gate_approval_type(initial_state)
        if approval_type:
            apply_approval(
                store, workflow, approval_type,
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


# ---- /import: an existing project from a GitHub repo -----------------------


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/import [url] -- the onboarding counterpart to /create, for a
    project that already exists somewhere on GitHub. `/import <url>` in
    one shot works (unlike /create, a URL is naturally one argument, not
    something worth a follow-up question); bare `/import` falls back to
    asking for it, same two-step shape /create already uses.

    A bare https://github.com/<owner>/<repo> URL pasted with NO command at
    all also triggers this (see handle_message) -- the primary way this
    is meant to be used: paste the link, onboarding starts.
    """
    args = context.args or []
    if args:
        await _import_project_from_chat(update, context, args[0])
        return

    key = (update.effective_chat.id, update.message.message_thread_id)
    context.bot_data["pending_import"][key] = True
    await update.message.reply_text(
        "Send the GitHub repository URL to import "
        "(https://github.com/<owner>/<repo>)."
    )


async def _import_project_from_chat(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
) -> None:
    match = _GITHUB_URL_SEARCH_RE.search(text)
    if not match:
        await update.message.reply_text(
            "That doesn't look like a GitHub URL -- expected "
            "https://github.com/<owner>/<repo>. Run /import again to retry."
        )
        return
    url = match.group(0)

    try:
        name = repo_name_from_url(url)
    except InvalidRepositoryUrl as exc:
        await update.message.reply_text(f"Can't import that URL: {exc}")
        return

    registry = context.bot_data["registry"]
    try:
        registry.get(name)
    except ProjectNotFound:
        pass
    else:
        await update.message.reply_text(
            f"A project named {name!r} already exists -- rename or remove "
            f"the old one first."
        )
        return

    await update.message.reply_text(f"Cloning {url}...")
    host_path = _default_host_root() / name
    try:
        # A real network call -- off the event loop like every Hermes
        # call already is (module docstring, brief S24), for the same
        # reason: it must not hold up every other chat's updates while it
        # runs.
        await asyncio.to_thread(clone, url, host_path)
    except (InvalidRepositoryUrl, ProjectGitError) as exc:
        await update.message.reply_text(f"Couldn't clone that repository: {exc}")
        return

    config_dir = _config_dir(context.bot_data)
    workflow = WorkflowDefinition.load(config_dir / "workflow.yaml")
    state_path = host_path.parent / ".agentic-dev" / name
    _entry, store = create_project(
        registry, name=name, host_path=str(host_path), state_path=str(state_path),
        workflow=workflow,
    )
    approver = f"telegram:{update.effective_user.id}"
    try:
        next_state = apply_approval(store, workflow, IMPORT_PROJECT_APPROVAL, approver=approver)
    except (ApprovalError, WorkflowError) as exc:
        # The shipped workflow.yaml always declares IMPORT_PROJECT on
        # backlog -- this only fires for a customized workflow that
        # dropped it, which /import can't do anything sensible about.
        await update.message.reply_text(
            f"Cloned, but this workflow doesn't support importing "
            f"({exc}). Registered as {name!r}; use /link to drive it "
            f"manually if that's still useful."
        )
        return

    await update.message.reply_text(
        f"Imported {name!r} at {host_path} -- now at state {next_state!r}."
    )

    chat_id = update.effective_chat.id
    try:
        thread_id = create_forum_topic(context.bot.token, chat_id, name)
    except ForumTopicError as exc:
        await update.message.reply_text(
            f"Project imported, but couldn't create a forum topic ({exc}). "
            f"Use /link {name} in a chat/topic to drive it manually instead."
        )
        return

    registry.link_telegram(name, chat_id=chat_id, thread_id=thread_id)
    await context.bot.send_message(
        chat_id=chat_id, message_thread_id=thread_id,
        text=f"This topic now drives project {name!r}.",
    )

    # Unlike a brand-new project's first (discovery) turn, onboarding's
    # first turn needs no human-supplied content -- the auditor just
    # explores on its own. Auto-continue the same way an ordinary approval
    # does: enqueued on the shared job queue, never run inline (a Hermes
    # call can take minutes; see the module docstring's own reasoning for
    # why handle_message never blocks on one either). Guarded by
    # runs_agent the same way cli.py's _approve_and_continue is, in case a
    # customized workflow routes IMPORT_PROJECT somewhere that doesn't run
    # an agent at all.
    next_state_obj = workflow.get(next_state)
    if next_state_obj.runs_agent:
        message = default_continuation_message(IMPORT_PROJECT_APPROVAL, next_state)
        job = TurnJob(project_id=name, chat_id=chat_id, thread_id=thread_id, human_message=message)
        await context.bot_data["inbox"].put(job)


# ---- plain-text turns -------------------------------------------------------


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = (update.effective_chat.id, update.message.message_thread_id)
    text = update.message.text.strip()

    pending_create = context.bot_data["pending_create"]
    if key in pending_create:
        del pending_create[key]
        await _create_project_from_chat(update, context, text)
        return

    pending_import = context.bot_data["pending_import"]
    if key in pending_import:
        del pending_import[key]
        await _import_project_from_chat(update, context, text)
        return

    registry = context.bot_data["registry"]
    thread_id = update.message.message_thread_id
    try:
        entry = resolve_project(registry, chat_id=update.effective_chat.id, thread_id=thread_id)
    except RoutingError as exc:
        # Nothing is linked here yet -- a bare GitHub URL is the primary,
        # command-free way importing an existing project is meant to
        # work: paste the link, onboarding starts. An ALREADY-linked
        # chat/topic never reaches this branch, so a URL pasted mid-
        # conversation there is just ordinary turn content, never
        # hijacked into a new import.
        if _GITHUB_URL_SEARCH_RE.search(text):
            await _import_project_from_chat(update, context, text)
            return
        await update.message.reply_text(str(exc))
        return

    job = TurnJob(
        project_id=entry.name, chat_id=update.effective_chat.id,
        thread_id=thread_id, human_message=update.message.text,
    )
    await context.bot_data["inbox"].put(job)
    await update.message.reply_text("Working on it...")


# Telegram hard-caps a single message at 4096 characters and rejects the
# whole send with `BadRequest: Message is too long` above it -- confirmed
# live: a real agent response tripped this with nothing chunking it first,
# silently dropping the actual turn output from the chat (only the outer
# "Something went wrong" fallback in inbox_worker's except block reached
# the user). 4000 is a safe margin under the real limit, not a measured
# exact boundary.
_TELEGRAM_MESSAGE_LIMIT = 4000


def _split_for_telegram(text: str, limit: int = _TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks Telegram will accept, preferring paragraph
    boundaries (blank lines) so a split doesn't land mid-sentence. Falls
    back to a hard slice for a single paragraph that alone exceeds the
    limit (e.g. an unbroken code block or stack trace in the response).
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
        else:
            for i in range(0, len(paragraph), limit):
                chunks.append(paragraph[i : i + limit])
            current = ""
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


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
        # A State.next_by_approval state (onboarding) has several valid
        # approval types instead of one -- one button per type, each its
        # own callback token/payload, so clicking any of them resolves to
        # exactly that type (apply_approval() -> advance() picks the
        # matching destination). An ordinary single-approval_type state
        # still gets exactly the one button it always did.
        approval_types = (
            list(state.next_by_approval) if state.next_by_approval
            else ([state.approval_type] if state.approval_type else [])
        )
        buttons = []
        for approval_type in approval_types:
            token = secrets.token_hex(4)
            app.bot_data["callback_table"][token] = CallbackPayload(
                project_id=job.project_id, approval_type=approval_type,
                artifact_revision=state_data.get("artifact_revision", 0),
            )
            buttons.append(InlineKeyboardButton(f"Approve ({approval_type})", callback_data=token))
        if buttons:
            reply_markup = InlineKeyboardMarkup([[b] for b in buttons])

    if outcome.failed:
        reason = outcome.failure_reason or "no reason reported"
        text = f"[turn failed] {reason}"
    else:
        text = outcome.response

    chunks = _split_for_telegram(text)
    for i, chunk in enumerate(chunks):
        await app.bot.send_message(
            chat_id=job.chat_id, message_thread_id=job.thread_id,
            text=chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None,
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

    # Auto-continue into the newly-unlocked state so approving doesn't need
    # a manual follow-up message just to kick off work the approval already
    # authorized -- see orchestrator.approval_flow.default_continuation_message.
    next_state_obj = workflow.get(next_state)
    if next_state_obj.runs_agent:
        message = default_continuation_message(payload.approval_type, next_state)
        job = TurnJob(
            project_id=payload.project_id,
            chat_id=query.message.chat.id,
            thread_id=query.message.message_thread_id,
            human_message=message,
        )
        await context.bot_data["inbox"].put(job)
        await query.message.reply_text("Continuing automatically...")
