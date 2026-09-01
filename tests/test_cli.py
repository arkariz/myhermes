"""CLI driver -- exercised through main(argv), against a temp registry and
config so nothing touches the real config/projects.yaml. hermes_run is
monkeypatched; no subprocess or network call happens here.
"""

from dataclasses import replace

import pytest

import agentic_dev.entrypoints.cli as cli_module
import agentic_dev.ports.agent_runtime as agent_runtime_module
from agentic_dev.ports.agent_runtime import HermesResult
from agentic_dev.settings import Settings


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


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "workflow.yaml").write_text(WORKFLOW_YAML)
    (config_dir / "agents.yaml").write_text(AGENTS_YAML)
    (config_dir / "models.yaml").write_text(MODELS_YAML)

    # Regression: this used to write to tmp_path/"souls", but cli.py read
    # settings.souls_dir == config_dir/"souls" -- so no CLI test ever
    # actually loaded a soul file, and every one still passed, because
    # RoleSoulProvider degrades a missing soul to [] rather than raising.
    souls_dir = config_dir / "souls"
    souls_dir.mkdir()
    (souls_dir / "planner.md").write_text("You are the planner.")

    # settings.py replaces the old CONFIG_DIR/REPO_ROOT module constants --
    # this is the "test/bootstrap seam" the module was designed around:
    # swap the whole frozen instance rather than patch individual paths.
    monkeypatch.setattr(cli_module, "settings", Settings(
        workspace=tmp_path,
        dart_indexer_dir=tmp_path / "tools" / "dart_indexer",
        runtime_url=None,
        telegram_bot_token=None,
        telegram_forum_chat_id=None,
        telegram_default_host_root=tmp_path / "projects",
    ))

    return tmp_path


def fake_success(session_id="sess-1", response="OK."):
    def _fake(request, usage_file=None):
        return HermesResult(
            response=response, usage={"failed": False, "session_id": session_id},
            exit_code=0, session_id=session_id,
        )
    return _fake


def test_project_new_creates_registry_entry_and_state(cli_env, capsys):
    rc = cli_module.main([
        "project", "new", "toy", "--host-path", str(cli_env / "host" / "toy"),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Created project 'toy'" in out

    entry = cli_module._registry().get("toy")
    assert entry.host_path == str(cli_env / "host" / "toy")


def test_project_new_twice_raises(cli_env):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    from agentic_dev.adapters.registry import ProjectAlreadyExists
    with pytest.raises(ProjectAlreadyExists):
        cli_module.main(["project", "new", "toy", "--host-path", "p"])


def test_turn_runs_and_prints_response(cli_env, monkeypatch, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", str(cli_env / "host" / "toy")])
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="Here's the PRD."))

    rc = cli_module.main(["turn", "toy", "Build a habit tracker."])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Here's the PRD." in out


def test_status_reports_workflow_state(cli_env, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    rc = cli_module.main(["status", "toy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "workflow_state: planning" in out


def test_approve_advances_state_on_matching_type(cli_env, monkeypatch, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())
    cli_module.main(["turn", "toy", "Build a habit tracker."])

    rc = cli_module.main(["approve", "toy", "APPROVE_PRD"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "'planning' -> 'done'" in out

    status_rc = cli_module.main(["status", "toy"])
    assert status_rc == 0
    assert "workflow_state: done" in capsys.readouterr().out


def test_approve_with_wrong_type_is_rejected(cli_env, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    rc = cli_module.main(["approve", "toy", "APPROVE_MERGE"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "REJECTED" in err


def test_approve_clears_the_session_forcing_a_rebuild_next_state(cli_env, monkeypatch):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(session_id="sess-1"))
    cli_module.main(["turn", "toy", "Build a habit tracker."])

    entry = cli_module._registry().get("toy")
    from agentic_dev.adapters.storage.store import ProjectStore
    store = ProjectStore(entry.state_path)
    assert store.read_state()["session"]["hermes_session_id"] == "sess-1"

    cli_module.main(["approve", "toy", "APPROVE_PRD"])
    assert store.read_state().get("session") is None


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


def test_approve_auto_continues_into_a_state_that_runs_an_agent(cli_env, monkeypatch, capsys):
    """Regression/feature test: approving used to just flip the state and
    stop, leaving the human to type a follow-up message purely to unlock
    work the approval already authorized. Now the newly-entered state's
    first turn runs automatically."""
    (cli_env / "config" / "workflow.yaml").write_text(TWO_STATE_WORKFLOW_YAML)
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="Discovery notes."))
    cli_module.main(["turn", "toy", "Build a habit tracker."])
    capsys.readouterr()  # drain prior output

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="Planning kicked off."))
    rc = cli_module.main(["approve", "toy", "APPROVE_DISCOVERY"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Approved. 'discovery' -> 'planning'" in out
    assert "Planning kicked off." in out

    status_rc = cli_module.main(["status", "toy"])
    assert status_rc == 0
    assert "workflow_state: planning" in capsys.readouterr().out


def test_approve_does_not_auto_continue_into_a_terminal_state(cli_env, monkeypatch, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", "p"])
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())
    cli_module.main(["turn", "toy", "Build a habit tracker."])
    capsys.readouterr()

    calls = []
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: calls.append(1) or fake_success()(request, usage_file))
    rc = cli_module.main(["approve", "toy", "APPROVE_PRD"])

    assert rc == 0
    assert calls == []  # "done" is terminal -- no auto-triggered turn


# ---- Telegram forum-topic auto-creation on `project new` -------------------


def test_project_new_without_telegram_env_skips_topic_creation(cli_env, monkeypatch):
    # cli_env's default Settings already has no telegram token/chat_id set.
    called = []
    monkeypatch.setattr(cli_module, "create_forum_topic", lambda *a, **k: called.append(1) or 1)

    cli_module.main(["project", "new", "toy", "--host-path", "p"])

    assert called == []
    entry = cli_module._registry().get("toy")
    assert entry.telegram_chat_id is None


def test_project_new_with_telegram_env_creates_and_links_topic(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "settings", replace(
        cli_module.settings, telegram_bot_token="fake-token", telegram_forum_chat_id=555,
    ))
    captured_args = {}

    def fake_create(token, chat_id, name):
        captured_args.update(token=token, chat_id=chat_id, name=name)
        return 42

    monkeypatch.setattr(cli_module, "create_forum_topic", fake_create)

    cli_module.main(["project", "new", "toy", "--host-path", "p"])

    assert captured_args == {"token": "fake-token", "chat_id": 555, "name": "toy"}
    entry = cli_module._registry().get("toy")
    assert entry.telegram_chat_id == 555
    assert entry.telegram_thread_id == 42
    assert "created topic 'toy'" in capsys.readouterr().out


def test_project_new_reports_but_survives_a_telegram_failure(cli_env, monkeypatch, capsys):
    from agentic_dev.adapters.telegram.topics import ForumTopicError

    monkeypatch.setattr(cli_module, "settings", replace(
        cli_module.settings, telegram_bot_token="fake-token", telegram_forum_chat_id=555,
    ))

    def fake_create(token, chat_id, name):
        raise ForumTopicError("chat is not a forum")

    monkeypatch.setattr(cli_module, "create_forum_topic", fake_create)

    rc = cli_module.main(["project", "new", "toy", "--host-path", "p"])

    assert rc == 0  # project creation itself must not fail
    entry = cli_module._registry().get("toy")
    assert entry.telegram_chat_id is None
    assert "could not create a forum topic" in capsys.readouterr().err
