"""TurnRunner: the piece that ties state machine + sessions + context
builder + runtime.hermes + store together for one turn.

hermes_run is monkeypatched -- these tests never touch a subprocess, a
network call, or real money. What's under test is the WIRING: does a
boundary turn build full context and open a session; does a continuation
turn resume and send a delta; does a failed turn bump attempts without
advancing; does exhausting attempts block the project.
"""

import pytest

import orchestrator.jobs as jobs_module
from orchestrator.config import AgentsConfig, ModelsConfig
from orchestrator.jobs import TurnBlocked, TurnRunner
from orchestrator.state_machine import WorkflowDefinition
from orchestrator.store import ProjectStore
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
    next: implementation
  implementation:
    kind: autonomous
    role: builder
    max_attempts: 2
    on_failure: blocked
    next: done
  done:
    kind: terminal
  blocked:
    kind: terminal
    recoverable: true
"""

AGENTS_YAML = """
defaults:
  toolsets: [skills]
  context_mode: assembled
roles:
  planner:
    context_mode: assembled
    toolsets: [skills]
    budget: {max_input_tokens: 12000, max_output_tokens: 4000}
  builder:
    context_mode: guided
    toolsets: [terminal]
    budget: {max_input_tokens: 20000, max_output_tokens: 6000}
    read_budget: {max_files: 10, max_bytes: 10000, max_tool_calls: 20}
"""

MODELS_YAML = """
routing:
  planner: {provider: openrouter, model: openai/gpt-4o-mini}
  builder: {provider: openrouter, model: openai/gpt-4o-mini}
"""


@pytest.fixture
def runner(tmp_path):
    (tmp_path / "workflow.yaml").write_text(WORKFLOW_YAML)
    (tmp_path / "agents.yaml").write_text(AGENTS_YAML)
    (tmp_path / "models.yaml").write_text(MODELS_YAML)
    (tmp_path / "souls").mkdir()
    (tmp_path / "souls" / "planner.md").write_text("You are the planner.")

    state_root = tmp_path / "agent-state"
    store = ProjectStore(state_root)
    workflow = WorkflowDefinition.load(tmp_path / "workflow.yaml")
    agents = AgentsConfig.load(tmp_path / "agents.yaml")
    models = ModelsConfig.load(tmp_path / "models.yaml")

    return TurnRunner(
        project_id="toy", store=store, workflow=workflow, agents=agents,
        models=models, souls_dir=str(tmp_path / "souls"),
    )


def fake_success(session_id="sess-1", response="OK.", input_tokens=100):
    def _fake(request, usage_file=None):
        return HermesResult(
            response=response,
            usage={"failed": False, "session_id": session_id, "input_tokens": input_tokens},
            exit_code=0,
            session_id=session_id,
        )
    return _fake


def fake_failure():
    def _fake(request, usage_file=None):
        return HermesResult(
            response="", usage={"failed": True, "failure": "boom"}, exit_code=1, session_id=None,
        )
    return _fake


# ---- full boundary turn ---------------------------------------------------


def test_first_turn_is_a_full_boundary_turn(monkeypatch, runner):
    captured = {}

    def fake_run(request, usage_file=None):
        captured["request"] = request
        captured["usage_file"] = usage_file
        return HermesResult(
            response="Here is the PRD.", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(jobs_module, "hermes_run", fake_run)
    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.failed is False
    assert captured["request"].resume_session_id is None  # full turn, no resume
    assert captured["usage_file"] is not None              # -z path gets --usage-file
    assert "You are the planner." in captured["request"].prompt
    assert "Build a habit tracker." in captured["request"].prompt


def test_first_turn_opens_a_new_session(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")

    state = runner.store.read_state()
    assert state["session"]["hermes_session_id"] == "sess-1"
    assert state["session"]["turns"] == 1


def test_response_and_manifest_are_persisted(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response="The PRD text."))
    outcome = runner.run_turn("Build a habit tracker.")

    assert runner.store.read_turn_artifact(outcome.turn_id, "response.md") == "The PRD text."
    manifest = runner.store.read_turn_artifact(outcome.turn_id, "manifest.yaml")
    assert "planner" in manifest


# ---- continuation turn -----------------------------------------------------


def test_second_turn_in_the_same_state_resumes(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")

    captured = {}

    def fake_run(request, usage_file=None):
        captured["request"] = request
        captured["usage_file"] = usage_file
        return HermesResult(
            response="Revised PRD.", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(jobs_module, "hermes_run", fake_run)
    runner.run_turn("Make it support two users.")

    assert captured["request"].resume_session_id == "sess-1"
    assert captured["usage_file"] is None  # chat -q --resume path, no --usage-file


def test_continuation_turn_increments_session_turn_count(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")
    runner.run_turn("Make it support two users.")

    state = runner.store.read_state()
    assert state["session"]["turns"] == 2
    assert state["session"]["hermes_session_id"] == "sess-1"  # not a new session


# ---- failure and retry bounding --------------------------------------------


def test_failed_turn_increments_attempts_without_advancing(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_failure())
    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.failed is True
    assert runner.store.read_state()["attempts"] == 1


def test_successful_turn_resets_attempts(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_failure())
    runner.run_turn("try 1")
    assert runner.store.read_state()["attempts"] == 1

    monkeypatch.setattr(jobs_module, "hermes_run", fake_success())
    runner.run_turn("try 2")
    assert runner.store.read_state()["attempts"] == 0


def test_autonomous_state_blocks_after_max_attempts(monkeypatch, runner):
    runner.store.update_state(workflow_state="implementation", attempts=2)
    with pytest.raises(TurnBlocked):
        runner.run_turn("build it")
    assert runner.store.read_state()["workflow_state"] == "blocked"


# ---- states that don't run an agent ----------------------------------------


def test_gate_or_terminal_state_refuses_to_run_a_turn(runner):
    runner.store.update_state(workflow_state="done")
    with pytest.raises(TurnBlocked):
        runner.run_turn("anything")


# ---- decisions get recorded from the response ------------------------------


def test_decision_block_in_response_is_recorded(monkeypatch, runner):
    response = (
        "Here's my reasoning.\n\n"
        "```decision\nid: 001-scope\ndecision: Single-child MVP\n```\n"
    )
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response=response))
    runner.run_turn("Build a habit tracker.")

    decision_file = runner.store.decisions_dir() / "001-scope.md"
    assert decision_file.exists()
    assert "Single-child MVP" in decision_file.read_text()


# ---- summarization (orchestrator/summarizer.py) -----------------------------


MODELS_YAML_WITH_SUMMARIZER = MODELS_YAML + """
  summarizer: {provider: openrouter, model: deepseek/deepseek-v3}
"""


@pytest.fixture
def runner_with_summarizer(tmp_path):
    (tmp_path / "workflow.yaml").write_text(WORKFLOW_YAML)
    (tmp_path / "agents.yaml").write_text(AGENTS_YAML)
    (tmp_path / "models.yaml").write_text(MODELS_YAML_WITH_SUMMARIZER)
    (tmp_path / "souls").mkdir()
    (tmp_path / "souls" / "planner.md").write_text("You are the planner.")

    store = ProjectStore(tmp_path / "agent-state")
    workflow = WorkflowDefinition.load(tmp_path / "workflow.yaml")
    agents = AgentsConfig.load(tmp_path / "agents.yaml")
    models = ModelsConfig.load(tmp_path / "models.yaml")
    return TurnRunner(
        project_id="toy", store=store, workflow=workflow, agents=agents,
        models=models, souls_dir=str(tmp_path / "souls"),
    )


def test_no_summarizer_route_means_no_summary_file(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success())
    runner.run_turn("Build a habit tracker.")
    assert runner.store.read_summary("planning") is None


def test_summarizer_route_present_updates_the_summary(monkeypatch, runner_with_summarizer):
    import orchestrator.summarizer as summarizer_module

    monkeypatch.setattr(jobs_module, "hermes_run", fake_success())
    monkeypatch.setattr(summarizer_module, "hermes_run", lambda request: HermesResult(
        response="Summary: building a habit tracker.", usage={"failed": False},
        exit_code=0, session_id=None,
    ))

    runner_with_summarizer.run_turn("Build a habit tracker.")

    assert runner_with_summarizer.store.read_summary("planning") == (
        "Summary: building a habit tracker."
    )


def test_summarizer_is_not_called_when_the_main_turn_fails(monkeypatch, runner_with_summarizer):
    import orchestrator.summarizer as summarizer_module

    called = []
    monkeypatch.setattr(jobs_module, "hermes_run", fake_failure())
    monkeypatch.setattr(summarizer_module, "hermes_run", lambda request: called.append(1))

    runner_with_summarizer.run_turn("Build a habit tracker.")

    assert called == []
    assert runner_with_summarizer.store.read_summary("planning") is None


def test_summarizer_failure_does_not_fail_the_turn(monkeypatch, runner_with_summarizer):
    import orchestrator.summarizer as summarizer_module

    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response="The PRD text."))
    monkeypatch.setattr(summarizer_module, "hermes_run", lambda request: HermesResult(
        response="", usage={"failed": True}, exit_code=1, session_id=None,
    ))

    outcome = runner_with_summarizer.run_turn("Build a habit tracker.")

    assert outcome.failed is False
    assert outcome.response == "The PRD text."


# ---- runtime_url: routing Hermes calls through runtime/server.py -----------


def test_no_runtime_url_calls_hermes_run_in_process(monkeypatch, runner):
    # runner.runtime_url is None by default -- confirm the in-process path
    # runs at all (the other two tests below confirm the HTTP path is used
    # *instead* when runtime_url is set, and that it alone runs, not both).
    assert runner.runtime_url is None
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response="direct call"))

    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.response == "direct call"


def test_runtime_url_set_calls_the_http_client_instead(monkeypatch, runner):
    runner.runtime_url = "http://agent-runtime:8000"
    captured = {}

    def fake_client_run(request, usage_file=None, base_url=None):
        captured["base_url"] = base_url
        captured["prompt"] = request.prompt
        return HermesResult(
            response="via http", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    called_direct = []
    monkeypatch.setattr(jobs_module, "runtime_client_run", fake_client_run)
    monkeypatch.setattr(jobs_module, "hermes_run", lambda *a, **k: called_direct.append(1))

    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.response == "via http"
    assert captured["base_url"] == "http://agent-runtime:8000"
    assert called_direct == []  # the in-process path must not also run


def test_runtime_client_error_becomes_a_runtime_error(monkeypatch, runner):
    from runtime.client import RuntimeClientError

    runner.runtime_url = "http://agent-runtime:8000"

    def raise_client_error(request, usage_file=None, base_url=None):
        raise RuntimeClientError("could not reach runtime server")

    monkeypatch.setattr(jobs_module, "runtime_client_run", raise_client_error)

    with pytest.raises(RuntimeError, match="runtime server call failed"):
        runner.run_turn("Build a habit tracker.")


# ---- artifact versioning (orchestrator/artifact_versioning.py) -------------


def test_a_written_artifact_gets_committed(monkeypatch, runner):
    def fake_run_that_writes_the_artifact(request, usage_file=None):
        runner.store.artifact("prd.md").parent.mkdir(parents=True, exist_ok=True)
        runner.store.artifact("prd.md").write_text("# PRD v1", encoding="utf-8")
        return HermesResult(
            response="Drafted the PRD.", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(jobs_module, "hermes_run", fake_run_that_writes_the_artifact)
    runner.run_turn("Build a habit tracker.")

    from orchestrator.artifact_versioning import log
    entries = log(runner.store.artifacts_dir())
    assert len(entries) == 1
    assert "planning" in entries[0]["message"]


def test_a_turn_with_no_artifact_change_commits_nothing(monkeypatch, runner):
    monkeypatch.setattr(jobs_module, "hermes_run", fake_success(response="Just a question."))
    runner.run_turn("Build a habit tracker.")

    from orchestrator.artifact_versioning import log
    assert log(runner.store.artifacts_dir()) == []


def test_a_failed_turn_does_not_commit(monkeypatch, runner):
    def fake_run_that_writes_then_fails(request, usage_file=None):
        runner.store.artifact("prd.md").parent.mkdir(parents=True, exist_ok=True)
        runner.store.artifact("prd.md").write_text("half-written", encoding="utf-8")
        return HermesResult(
            response="", usage={"failed": True, "failure": "boom"}, exit_code=1, session_id=None,
        )

    monkeypatch.setattr(jobs_module, "hermes_run", fake_run_that_writes_then_fails)
    runner.run_turn("Build a habit tracker.")

    from orchestrator.artifact_versioning import log
    assert log(runner.store.artifacts_dir()) == []
