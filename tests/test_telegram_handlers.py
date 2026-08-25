"""telegram_bot.handlers -- exercised with lightweight fakes for Update and
context, not a real Bot or network call. Only the handlers' own logic
(routing lookups, enqueueing, approval application) is under test here;
python-telegram-bot's own wire format is out of scope.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

import runtime.agent_runtime as agent_runtime_module
from orchestrator.config import AgentsConfig, ModelsConfig
from orchestrator.registry import ProjectRegistry
from orchestrator.state_machine import WorkflowDefinition
from orchestrator.store import ProjectStore
from runtime.agent_runtime import HermesResult
from telegram_bot import handlers

AGENTS_YAML = """
defaults: {toolsets: [skills], context_mode: assembled}
roles:
  planner:
    context_mode: assembled
    toolsets: [skills]
    budget: {max_input_tokens: 12000, max_output_tokens: 4000}
"""

MODELS_YAML = """
routing:
  planner: {provider: openrouter, model: openai/gpt-4o-mini}
"""

WORKFLOW_YAML = """
initial: planning
states:
  planning:
    kind: collaborative
    role: planner
    artifact: prd.md
    completion: approval
    approval_type: APPROVE_PRD
    next: done
  done:
    kind: terminal
"""


def make_update(*, chat_id=100, thread_id=None, text=None, args=None, user_id=7, first_name="Ada"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.reply_text = AsyncMock()
    return update


def make_context(bot_data, *, args=None):
    context = MagicMock()
    context.bot_data = bot_data
    context.args = args or []
    return context


@pytest.fixture
def bot_data(tmp_path):
    (tmp_path / "workflow.yaml").write_text(WORKFLOW_YAML)
    (tmp_path / "agents.yaml").write_text(AGENTS_YAML)
    (tmp_path / "models.yaml").write_text(MODELS_YAML)
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    (souls_dir / "planner.md").write_text("You are the planner.")
    return {
        "config_dir": tmp_path,
        "souls_dir": souls_dir,
        "registry": ProjectRegistry(tmp_path / "projects.yaml"),
        "inbox": asyncio.Queue(),
        "callback_table": {},
        "pending_create": {},
    }


def _new_project(bot_data, tmp_path, project_id="toy"):
    registry: ProjectRegistry = bot_data["registry"]
    state_path = tmp_path / "state" / project_id
    registry.register(project_id, host_path=str(tmp_path / "host"), state_path=str(state_path))
    store = ProjectStore(state_path)
    store.ensure_layout()
    store.write_state({"workflow_state": "planning", "attempts": 0, "artifact_revision": 0})
    return store


@pytest.mark.asyncio
async def test_link_attaches_chat_to_project(bot_data, tmp_path):
    _new_project(bot_data, tmp_path)
    update = make_update(chat_id=555, thread_id=None)
    context = make_context(bot_data, args=["toy"])

    await handlers.cmd_link(update, context)

    entry = bot_data["registry"].find_by_telegram(chat_id=555, thread_id=None)
    assert entry is not None and entry.name == "toy"
    update.message.reply_text.assert_awaited_once()
    assert "toy" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_link_unknown_project_replies_with_error(bot_data):
    update = make_update(args=["ghost"])
    context = make_context(bot_data, args=["ghost"])

    await handlers.cmd_link(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "No such project" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_status_before_linking_asks_to_link(bot_data):
    update = make_update()
    context = make_context(bot_data)

    await handlers.cmd_status(update, context)

    assert "/link" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_status_after_linking_reports_workflow_state(bot_data, tmp_path):
    _new_project(bot_data, tmp_path)
    bot_data["registry"].link_telegram("toy", chat_id=100, thread_id=None)
    update = make_update(chat_id=100)
    context = make_context(bot_data)

    await handlers.cmd_status(update, context)

    reply = update.message.reply_text.await_args.args[0]
    assert "workflow_state: planning" in reply


@pytest.mark.asyncio
async def test_message_without_link_asks_to_link(bot_data):
    update = make_update(chat_id=999, text="Build a habit tracker.")
    context = make_context(bot_data)

    await handlers.handle_message(update, context)

    assert "/link" in update.message.reply_text.await_args.args[0]
    assert bot_data["inbox"].empty()


@pytest.mark.asyncio
async def test_message_after_linking_enqueues_a_turn_job(bot_data, tmp_path):
    _new_project(bot_data, tmp_path)
    bot_data["registry"].link_telegram("toy", chat_id=100, thread_id=None)
    update = make_update(chat_id=100, text="Build a habit tracker.")
    context = make_context(bot_data)

    await handlers.handle_message(update, context)

    job = bot_data["inbox"].get_nowait()
    assert job.project_id == "toy"
    assert job.human_message == "Build a habit tracker."
    update.message.reply_text.assert_awaited_once()  # immediate ack, not the turn's response


@pytest.mark.asyncio
async def test_approve_callback_applies_a_valid_approval(bot_data, tmp_path):
    _new_project(bot_data, tmp_path)
    workflow = WorkflowDefinition.load(tmp_path / "workflow.yaml")
    entry = bot_data["registry"].get("toy")
    store = ProjectStore(entry.state_path)

    token = "abc123"
    bot_data["callback_table"][token] = handlers.CallbackPayload(
        project_id="toy", approval_type="APPROVE_PRD", artifact_revision=0,
    )

    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.data = token
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    context = make_context(bot_data)

    await handlers.handle_approve_callback(update, context)

    assert store.read_state()["workflow_state"] == "done"
    update.callback_query.message.reply_text.assert_awaited_once()
    assert "Approved" in update.callback_query.message.reply_text.await_args.args[0]
    assert token not in bot_data["callback_table"]  # single use


TWO_STATE_WORKFLOW_YAML = """
initial: discovery
states:
  discovery:
    kind: collaborative
    role: planner
    artifact: discovery.md
    completion: approval
    approval_type: APPROVE_DISCOVERY
    next: planning
  planning:
    kind: collaborative
    role: planner
    artifact: prd.md
    completion: approval
    approval_type: APPROVE_PRD
    next: done
  done:
    kind: terminal
"""


@pytest.mark.asyncio
async def test_approve_callback_auto_continues_into_a_state_that_runs_an_agent(bot_data, tmp_path):
    """Regression/feature test: approving used to just flip the state and
    stop, leaving the human to type a follow-up message purely to unlock
    work the approval already authorized. Now the newly-entered state's
    first turn is enqueued automatically, through the same inbox the
    worker already drains."""
    (tmp_path / "workflow.yaml").write_text(TWO_STATE_WORKFLOW_YAML)
    store = _new_project(bot_data, tmp_path)
    store.write_state({"workflow_state": "discovery", "attempts": 0, "artifact_revision": 0})

    token = "abc123"
    bot_data["callback_table"][token] = handlers.CallbackPayload(
        project_id="toy", approval_type="APPROVE_DISCOVERY", artifact_revision=0,
    )

    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.data = token
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.chat.id = 100
    update.callback_query.message.message_thread_id = None
    update.callback_query.message.reply_text = AsyncMock()
    context = make_context(bot_data)

    await handlers.handle_approve_callback(update, context)

    assert store.read_state()["workflow_state"] == "planning"
    assert bot_data["inbox"].qsize() == 1
    job = bot_data["inbox"].get_nowait()
    assert job.project_id == "toy"
    assert job.chat_id == 100
    assert "APPROVE_DISCOVERY" in job.human_message
    assert "planning" in job.human_message


@pytest.mark.asyncio
async def test_approve_callback_with_stale_revision_is_rejected(bot_data, tmp_path):
    _new_project(bot_data, tmp_path)
    store = ProjectStore(bot_data["registry"].get("toy").state_path)
    store.update_state(artifact_revision=5)  # the artifact moved on since the button was sent

    token = "stale1"
    bot_data["callback_table"][token] = handlers.CallbackPayload(
        project_id="toy", approval_type="APPROVE_PRD", artifact_revision=0,
    )

    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.data = token
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    context = make_context(bot_data)

    await handlers.handle_approve_callback(update, context)

    assert store.read_state()["workflow_state"] == "planning"  # unchanged
    assert "Not approved" in update.callback_query.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_approve_callback_with_expired_token_says_so(bot_data, tmp_path):
    update = make_update()
    update.callback_query = MagicMock()
    update.callback_query.data = "nonexistent"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    context = make_context(bot_data)

    await handlers.handle_approve_callback(update, context)

    assert "expired" in update.callback_query.message.reply_text.await_args.args[0]


# ---- the worker: _process_turn_job actually runs a turn and replies -------


def _fake_hermes(response="Here's the PRD.", session_id="sess-1"):
    def _run(request, usage_file=None):
        return HermesResult(
            response=response, usage={"failed": False, "session_id": session_id},
            exit_code=0, session_id=session_id,
        )
    return _run


def make_app(bot_data):
    app = MagicMock()
    app.bot_data = bot_data
    app.bot.send_message = AsyncMock()
    return app


@pytest.mark.asyncio
async def test_process_turn_job_sends_the_response_and_attaches_approve_button(
    bot_data, tmp_path, monkeypatch,
):
    _new_project(bot_data, tmp_path)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", _fake_hermes())
    app = make_app(bot_data)
    job = handlers.TurnJob(
        project_id="toy", chat_id=100, thread_id=None,
        human_message="Build a habit tracker.",
    )

    await handlers._process_turn_job(app, job)

    app.bot.send_message.assert_awaited_once()
    kwargs = app.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 100
    assert "Here's the PRD." in kwargs["text"]
    # planning is a collaborative, approval-completed state -- the button
    # should be attached so the human can approve straight from Telegram.
    assert kwargs["reply_markup"] is not None
    token = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert bot_data["callback_table"][token].approval_type == "APPROVE_PRD"


def _fake_hermes_failure(failure="rate limit exceeded (429)"):
    def _run(request, usage_file=None):
        return HermesResult(
            response="", usage={"failed": True, "failure": failure},
            exit_code=1, session_id=None,
        )
    return _run


@pytest.mark.asyncio
async def test_process_turn_job_does_not_offer_approval_on_a_failed_turn(
    bot_data, tmp_path, monkeypatch,
):
    """Regression test: state.requires_approval reflects the CURRENT
    workflow state, not whether THIS turn produced anything -- a failed
    turn leaves the state unchanged, so without this check the Approve
    button showed up next to an empty/error response. Found live after a
    turn failed on a rate-limited free-tier model."""
    _new_project(bot_data, tmp_path)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", _fake_hermes_failure())
    app = make_app(bot_data)
    job = handlers.TurnJob(
        project_id="toy", chat_id=100, thread_id=None,
        human_message="Build a habit tracker.",
    )

    await handlers._process_turn_job(app, job)

    kwargs = app.bot.send_message.await_args.kwargs
    assert kwargs["reply_markup"] is None
    assert "rate limit exceeded (429)" in kwargs["text"]
    assert "[turn failed]" in kwargs["text"]


# ---- message chunking (Telegram's 4096-char hard cap) -----------------------


def test_split_for_telegram_leaves_a_short_message_alone():
    assert handlers._split_for_telegram("short text") == ["short text"]


def test_split_for_telegram_splits_on_paragraph_boundaries():
    text = "\n\n".join(["para one", "para two", "x" * 3990])
    chunks = handlers._split_for_telegram(text, limit=4000)

    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    # nothing lost or reordered
    assert "\n\n".join(c for c in chunks if c).count("para one") == 1
    assert "para two" in "".join(chunks)
    assert "x" * 3990 in "".join(chunks)


def test_split_for_telegram_hard_slices_a_single_oversized_paragraph():
    text = "y" * 9000  # one unbroken block, no paragraph breaks at all
    chunks = handlers._split_for_telegram(text, limit=4000)

    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == text


@pytest.mark.asyncio
async def test_process_turn_job_sends_a_long_response_in_multiple_messages(
    bot_data, tmp_path, monkeypatch,
):
    """Regression test: a real agent response tripped Telegram's real
    `BadRequest: Message is too long` because nothing chunked it before
    sending -- the actual turn output never reached the user, only the
    generic "something went wrong" fallback did."""
    _new_project(bot_data, tmp_path)
    long_response = "\n\n".join(f"paragraph {i} " + "z" * 500 for i in range(20))
    monkeypatch.setattr(agent_runtime_module, "hermes_run", _fake_hermes(response=long_response))
    app = make_app(bot_data)
    job = handlers.TurnJob(
        project_id="toy", chat_id=100, thread_id=None,
        human_message="Build a habit tracker.",
    )

    await handlers._process_turn_job(app, job)

    calls = app.bot.send_message.await_args_list
    assert len(calls) > 1
    assert all(len(c.kwargs["text"]) <= handlers._TELEGRAM_MESSAGE_LIMIT for c in calls)
    # the Approve button only appears once, on the last message
    assert all(c.kwargs["reply_markup"] is None for c in calls[:-1])
    assert calls[-1].kwargs["reply_markup"] is not None
    # nothing was dropped
    reassembled = "".join(c.kwargs["text"] for c in calls)
    assert "paragraph 0 " in reassembled
    assert "paragraph 19 " in reassembled


@pytest.mark.asyncio
async def test_inbox_worker_drains_the_queue_and_survives_a_failed_job(bot_data, tmp_path, monkeypatch):
    _new_project(bot_data, tmp_path)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", _fake_hermes())
    app = make_app(bot_data)

    await bot_data["inbox"].put(handlers.TurnJob(
        project_id="does-not-exist", chat_id=1, thread_id=None, human_message="hi",
    ))
    await bot_data["inbox"].put(handlers.TurnJob(
        project_id="toy", chat_id=100, thread_id=None, human_message="Build a habit tracker.",
    ))

    worker = asyncio.create_task(handlers.inbox_worker(app))
    await bot_data["inbox"].join()
    worker.cancel()

    # first job (unknown project) failed and was reported; second still ran.
    assert app.bot.send_message.await_count == 2
    texts = [c.kwargs["text"] for c in app.bot.send_message.await_args_list]
    assert any("went wrong" in t for t in texts)
    assert any("Here's the PRD." in t for t in texts)


# ---- /create -----------------------------------------------------------


GATED_WORKFLOW_YAML = """
initial: backlog
states:
  backlog:
    kind: gate
    approval_type: START_PROJECT
    next: planning
  planning:
    kind: collaborative
    role: planner
    artifact: prd.md
    completion: approval
    approval_type: APPROVE_PRD
    next: done
  done:
    kind: terminal
"""


def make_create_context(bot_data, token="fake-bot-token"):
    context = make_context(bot_data)
    context.bot.token = token
    context.bot.send_message = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_create_sets_pending_and_asks_for_name(bot_data):
    update = make_update(chat_id=200)
    context = make_create_context(bot_data)

    await handlers.cmd_create(update, context)

    assert (200, None) in bot_data["pending_create"]
    assert "called" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_name_after_create_makes_a_new_project_and_topic(bot_data, tmp_path, monkeypatch):
    monkeypatch.setattr(
        handlers, "settings",
        dataclasses.replace(handlers.settings, telegram_default_host_root=tmp_path / "projects"),
    )
    created = {}

    def fake_create(token, chat_id, name):
        created["args"] = (token, chat_id, name)
        return 99

    monkeypatch.setattr(handlers, "create_forum_topic", fake_create)

    update = make_update(chat_id=200, text="/create")
    context = make_create_context(bot_data)
    await handlers.cmd_create(update, context)

    update2 = make_update(chat_id=200, text="my-new-app")
    await handlers.handle_message(update2, context)

    entry = bot_data["registry"].get("my-new-app")
    assert entry.host_path == str(tmp_path / "projects" / "my-new-app")
    assert entry.telegram_chat_id == 200
    assert entry.telegram_thread_id == 99
    assert created["args"] == ("fake-bot-token", 200, "my-new-app")
    assert (200, None) not in bot_data["pending_create"]  # consumed, single use
    context.bot.send_message.assert_awaited_once()  # confirmation posted into the new topic


@pytest.mark.asyncio
async def test_create_auto_clears_a_leading_gate_state(bot_data, tmp_path, monkeypatch):
    (bot_data["config_dir"] / "workflow.yaml").write_text(GATED_WORKFLOW_YAML)
    monkeypatch.setattr(
        handlers, "settings",
        dataclasses.replace(handlers.settings, telegram_default_host_root=tmp_path / "projects"),
    )
    monkeypatch.setattr(handlers, "create_forum_topic", lambda token, chat_id, name: 1)

    update = make_update(chat_id=200)
    context = make_create_context(bot_data)
    await handlers.cmd_create(update, context)
    await handlers.handle_message(make_update(chat_id=200, text="gated-app"), context)

    entry = bot_data["registry"].get("gated-app")
    store = ProjectStore(entry.state_path)
    assert store.read_state()["workflow_state"] == "planning"  # backlog gate auto-cleared


@pytest.mark.asyncio
async def test_create_with_invalid_name_is_rejected(bot_data):
    update = make_update(chat_id=200)
    context = make_create_context(bot_data)
    await handlers.cmd_create(update, context)

    update2 = make_update(chat_id=200, text="not a valid name!")
    await handlers.handle_message(update2, context)

    assert "letters, numbers, hyphens" in update2.message.reply_text.await_args.args[0]
    with pytest.raises(Exception):
        bot_data["registry"].get("not a valid name!")


@pytest.mark.asyncio
async def test_create_with_existing_name_is_rejected(bot_data, tmp_path):
    _new_project(bot_data, tmp_path, project_id="toy")
    update = make_update(chat_id=200)
    context = make_create_context(bot_data)
    await handlers.cmd_create(update, context)

    update2 = make_update(chat_id=200, text="toy")
    await handlers.handle_message(update2, context)

    assert "already exists" in update2.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_create_survives_a_forum_topic_failure(bot_data, tmp_path, monkeypatch):
    from telegram_bot.topics import ForumTopicError

    monkeypatch.setattr(
        handlers, "settings",
        dataclasses.replace(handlers.settings, telegram_default_host_root=tmp_path / "projects"),
    )

    def fail(token, chat_id, name):
        raise ForumTopicError("chat is not a forum")

    monkeypatch.setattr(handlers, "create_forum_topic", fail)

    update = make_update(chat_id=200)
    context = make_create_context(bot_data)
    await handlers.cmd_create(update, context)
    update2 = make_update(chat_id=200, text="orphan-app")
    await handlers.handle_message(update2, context)

    # project exists even though the topic couldn't be created
    entry = bot_data["registry"].get("orphan-app")
    assert entry.telegram_chat_id is None
    assert "couldn't create a forum topic" in update2.message.reply_text.await_args.args[0]
