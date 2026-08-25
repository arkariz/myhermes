"""CLI driver -- exercised through main(argv), against a temp registry and
config so nothing touches the real config/projects.yaml. hermes_run is
monkeypatched; no subprocess or network call happens here.
"""

import pytest

import orchestrator.cli as cli_module
import orchestrator.jobs as jobs_module
from runtime.hermes import HermesResult


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

    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    (souls_dir / "planner.md").write_text("You are the planner.")

    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli_module, "REPO_ROOT", tmp_path)

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
    from orchestrator.registry import ProjectAlreadyExists
    with pytest.raises(ProjectAlreadyExists):
        cli_module.main(["project", "new", "toy", "--host-path", "p"])


def test_turn_runs_and_prints_response(cli_env, monkeypatch, capsys):
    cli_module.main(["project", "new", "toy", "--host-path", str(cli_env / "host" / "toy")])
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response="Here's the PRD."))

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
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success())
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
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(session_id="sess-1"))
    cli_module.main(["turn", "toy", "Build a habit tracker."])

    entry = cli_module._registry().get("toy")
    from orchestrator.store import ProjectStore
    store = ProjectStore(entry.state_path)
    assert store.read_state()["session"]["hermes_session_id"] == "sess-1"

    cli_module.main(["approve", "toy", "APPROVE_PRD"])
    assert store.read_state().get("session") is None


# ---- Telegram forum-topic auto-creation on `project new` -------------------


def test_project_new_without_telegram_env_skips_topic_creation(cli_env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_FORUM_CHAT_ID", raising=False)
    called = []
    monkeypatch.setattr(cli_module, "create_forum_topic", lambda *a, **k: called.append(1) or 1)

    cli_module.main(["project", "new", "toy", "--host-path", "p"])

    assert called == []
    entry = cli_module._registry().get("toy")
    assert entry.telegram_chat_id is None


def test_project_new_with_telegram_env_creates_and_links_topic(cli_env, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_FORUM_CHAT_ID", "555")
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
    from telegram_bot.topics import ForumTopicError

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_FORUM_CHAT_ID", "555")

    def fake_create(token, chat_id, name):
        raise ForumTopicError("chat is not a forum")

    monkeypatch.setattr(cli_module, "create_forum_topic", fake_create)

    rc = cli_module.main(["project", "new", "toy", "--host-path", "p"])

    assert rc == 0  # project creation itself must not fail
    entry = cli_module._registry().get("toy")
    assert entry.telegram_chat_id is None
    assert "could not create a forum topic" in capsys.readouterr().err
