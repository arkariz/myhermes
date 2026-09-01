"""TurnRunner: the piece that ties state machine + sessions + context
builder + runtime.agent_runtime + store together for one turn.

hermes_run is monkeypatched (on runtime.agent_runtime, the one place both
TurnRunner and summarizer.py now get it from -- see that module's own
docstring) -- these tests never touch a subprocess, a network call, or
real money. What's under test is the WIRING: does a boundary turn build
full context and open a session; does a continuation turn resume and send
a delta; does a failed turn bump attempts without advancing; does
exhausting attempts block the project.
"""

import pytest

import agentic_dev.adapters.hermes.runtime as agent_runtime_module
from agentic_dev.domain.roles import AgentsConfig, ModelsConfig
from agentic_dev.app.turn_runner import TurnBlocked, TurnRunner
from agentic_dev.domain.workflow import WorkflowDefinition
from agentic_dev.adapters.storage.store import ProjectStore
from agentic_dev.adapters.hermes.runtime import HttpAgentRuntime, InProcessHermesRuntime
from agentic_dev.ports.agent_runtime import HermesResult


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
    models = ModelsConfig.load(tmp_path / "models.yaml", env={})

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

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.failed is False
    assert captured["request"].resume_session_id is None  # full turn, no resume
    assert captured["usage_file"] is not None              # -z path gets --usage-file
    assert "You are the planner." in captured["request"].prompt
    assert "Build a habit tracker." in captured["request"].prompt


def test_first_turn_opens_a_new_session(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")

    state = runner.store.read_state()
    assert state["session"]["hermes_session_id"] == "sess-1"
    assert state["session"]["turns"] == 1


def test_response_and_manifest_are_persisted(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="The PRD text."))
    outcome = runner.run_turn("Build a habit tracker.")

    assert runner.store.read_turn_artifact(outcome.turn_id, "response.md") == "The PRD text."
    manifest = runner.store.read_turn_artifact(outcome.turn_id, "manifest.yaml")
    assert "planner" in manifest


# ---- continuation turn -----------------------------------------------------


def test_second_turn_in_the_same_state_resumes(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")

    captured = {}

    def fake_run(request, usage_file=None):
        captured["request"] = request
        captured["usage_file"] = usage_file
        return HermesResult(
            response="Revised PRD.", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    runner.run_turn("Make it support two users.")

    assert captured["request"].resume_session_id == "sess-1"
    assert captured["usage_file"] is None  # chat -q --resume path, no --usage-file


def test_continuation_turn_increments_session_turn_count(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(session_id="sess-1"))
    runner.run_turn("Build a habit tracker.")
    runner.run_turn("Make it support two users.")

    state = runner.store.read_state()
    assert state["session"]["turns"] == 2
    assert state["session"]["hermes_session_id"] == "sess-1"  # not a new session


# ---- failure and retry bounding --------------------------------------------


def test_failed_turn_increments_attempts_without_advancing(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_failure())
    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.failed is True
    assert runner.store.read_state()["attempts"] == 1


def test_failed_turn_carries_the_real_failure_reason(monkeypatch, runner):
    # response is often empty on a real failure (no stdout to show) --
    # failure_reason is the only place a caller (CLI, Telegram) can find
    # out WHY, e.g. a 429 rate limit vs. a misconfigured provider.
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_failure())
    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.failed is True
    assert outcome.failure_reason == "boom"


def test_successful_turn_resets_attempts(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_failure())
    runner.run_turn("try 1")
    assert runner.store.read_state()["attempts"] == 1

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())
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
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response=response))
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
    models = ModelsConfig.load(tmp_path / "models.yaml", env={})
    return TurnRunner(
        project_id="toy", store=store, workflow=workflow, agents=agents,
        models=models, souls_dir=str(tmp_path / "souls"),
    )


def test_no_summarizer_route_means_no_summary_file(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())
    runner.run_turn("Build a habit tracker.")
    assert runner.store.read_summary("planning") is None


# These four tests all drive TWO Hermes calls per turn -- the main turn,
# then the summarizer's own one-shot call -- which now share exactly ONE
# agent_runtime.run(), not two independently-mockable module bindings the
# way jobs.py and summarizer.py used to each import separately (that
# duplication is exactly how a real routing bug shipped unnoticed once --
# see runtime/agent_runtime.py). A single fake distinguishes the two calls
# by prompt content: summarizer.py's own template has a fixed, recognizable
# opening line ("You maintain a running summary...").


def _is_summary_prompt(request) -> bool:
    return request.prompt.startswith("You maintain a running summary")


def test_summarizer_route_present_updates_the_summary(monkeypatch, runner_with_summarizer):
    def fake_run(request, usage_file=None):
        if _is_summary_prompt(request):
            return HermesResult(
                response="Summary: building a habit tracker.", usage={"failed": False},
                exit_code=0, session_id=None,
            )
        return fake_success()(request, usage_file=usage_file)

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)

    runner_with_summarizer.run_turn("Build a habit tracker.")

    assert runner_with_summarizer.store.read_summary("planning") == (
        "Summary: building a habit tracker."
    )


def test_summarizer_is_not_called_when_the_main_turn_fails(monkeypatch, runner_with_summarizer):
    calls = []

    def fake_run(request, usage_file=None):
        calls.append(request.prompt)
        return fake_failure()(request, usage_file=usage_file)

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)

    runner_with_summarizer.run_turn("Build a habit tracker.")

    assert len(calls) == 1  # only the main (failed) turn -- summarizer never ran
    assert not any(_is_summary_prompt_text(p) for p in calls)
    assert runner_with_summarizer.store.read_summary("planning") is None


def _is_summary_prompt_text(prompt: str) -> bool:
    return prompt.startswith("You maintain a running summary")


def test_summarizer_failure_does_not_fail_the_turn(monkeypatch, runner_with_summarizer):
    def fake_run(request, usage_file=None):
        if _is_summary_prompt(request):
            return HermesResult(response="", usage={"failed": True}, exit_code=1, session_id=None)
        return fake_success(response="The PRD text.")(request, usage_file=usage_file)

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)

    outcome = runner_with_summarizer.run_turn("Build a habit tracker.")

    assert outcome.failed is False
    assert outcome.response == "The PRD text."
    assert runner_with_summarizer.store.read_summary("planning") is None  # left untouched


def test_agent_runtime_is_shared_between_the_turn_and_the_summarizer(monkeypatch, runner_with_summarizer):
    """Regression test for a bug that shipped for real: jobs.py and
    summarizer.py used to each pick their OWN routing independently
    (a bare runtime_url string, re-checked per call in two different
    files) -- and the summarizer's copy of that check was simply missing
    once, live, in a container with no Hermes CLI at all. Now there is
    only one shared agent_runtime object, so this class of drift is
    structurally impossible: both calls go through it or neither does."""
    runner_with_summarizer.agent_runtime = HttpAgentRuntime(base_url="http://agent-runtime:8000")
    captured_urls = []

    def fake_client_run(request, usage_file=None, base_url=None):
        captured_urls.append(base_url)
        if _is_summary_prompt(request):
            return HermesResult(response="Summarized.", usage={"failed": False}, exit_code=0, session_id=None)
        return HermesResult(
            response="The PRD text.", usage={"failed": False, "session_id": "s1"},
            exit_code=0, session_id="s1",
        )

    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", fake_client_run)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: (_ for _ in ()).throw(
        AssertionError("should not call hermes_run in-process when agent_runtime is HttpAgentRuntime")
    ))

    runner_with_summarizer.run_turn("Build a habit tracker.")

    assert captured_urls == ["http://agent-runtime:8000", "http://agent-runtime:8000"]
    assert runner_with_summarizer.store.read_summary("planning") == "Summarized."


# ---- runtime_url: routing Hermes calls through runtime/server.py -----------


def test_no_runtime_url_calls_hermes_run_in_process(monkeypatch, runner):
    # runner.agent_runtime is an InProcessHermesRuntime by default -- confirm
    # the in-process path runs at all (the other two tests below confirm the
    # HTTP path is used *instead* when an HttpAgentRuntime is set, and that
    # it alone runs, not both).
    assert isinstance(runner.agent_runtime, InProcessHermesRuntime)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="direct call"))

    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.response == "direct call"


def test_runtime_url_set_calls_the_http_client_instead(monkeypatch, runner):
    runner.agent_runtime = HttpAgentRuntime(base_url="http://agent-runtime:8000")
    captured = {}

    def fake_client_run(request, usage_file=None, base_url=None):
        captured["base_url"] = base_url
        captured["prompt"] = request.prompt
        return HermesResult(
            response="via http", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    called_direct = []
    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", fake_client_run)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda *a, **k: called_direct.append(1))

    outcome = runner.run_turn("Build a habit tracker.")

    assert outcome.response == "via http"
    assert captured["base_url"] == "http://agent-runtime:8000"
    assert called_direct == []  # the in-process path must not also run


def test_runtime_client_error_becomes_a_runtime_error(monkeypatch, runner):
    from agentic_dev.adapters.hermes.http_client import RuntimeClientError

    runner.agent_runtime = HttpAgentRuntime(base_url="http://agent-runtime:8000")

    def raise_client_error(request, usage_file=None, base_url=None):
        raise RuntimeClientError("could not reach runtime server")

    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", raise_client_error)

    with pytest.raises(RuntimeError, match="runtime server call failed"):
        runner.run_turn("Build a habit tracker.")


# ---- Hermes cwd: where a `file`-toolset role's own writes land -------------


def test_assembled_role_gets_the_agent_state_root_as_cwd(monkeypatch, runner):
    # runner's default state is "planning" -- role: planner, context_mode:
    # assembled. Regression test: granting the `file` toolset alone did
    # nothing live, because nothing told the agent's own tool calls where
    # to resolve "artifacts/prd.md" against.
    captured = {}

    def fake_run(request, usage_file=None):
        captured["cwd"] = request.cwd
        return HermesResult(
            response="ok", usage={"failed": False, "session_id": "s1"},
            exit_code=0, session_id="s1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    runner.run_turn("Build a habit tracker.")

    assert captured["cwd"] == runner.store.root


def test_guided_role_gets_project_source_root_as_cwd(monkeypatch, runner, tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    runner.project_source_root = project
    runner.store.update_state(workflow_state="implementation", attempts=0)
    captured = {}

    def fake_run(request, usage_file=None):
        captured["cwd"] = request.cwd
        return HermesResult(
            response="ok", usage={"failed": False, "session_id": "s1"},
            exit_code=0, session_id="s1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    runner.run_turn("build it")

    assert captured["cwd"] == project


def test_guided_role_with_no_project_source_gets_no_cwd(monkeypatch, runner):
    runner.store.update_state(workflow_state="implementation", attempts=0)
    captured = {}

    def fake_run(request, usage_file=None):
        captured["cwd"] = request.cwd
        return HermesResult(
            response="ok", usage={"failed": False, "session_id": "s1"},
            exit_code=0, session_id="s1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    runner.run_turn("build it")

    assert captured["cwd"] is None


# ---- artifact versioning (orchestrator/artifact_versioning.py) -------------


def test_a_written_artifact_gets_committed(monkeypatch, runner):
    def fake_run_that_writes_the_artifact(request, usage_file=None):
        runner.store.artifact("prd.md").parent.mkdir(parents=True, exist_ok=True)
        runner.store.artifact("prd.md").write_text("# PRD v1", encoding="utf-8")
        return HermesResult(
            response="Drafted the PRD.", usage={"failed": False, "session_id": "sess-1"},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run_that_writes_the_artifact)
    runner.run_turn("Build a habit tracker.")

    from agentic_dev.adapters.git.artifacts import log
    entries = log(runner.store.artifacts_dir())
    assert len(entries) == 1
    assert "planning" in entries[0]["message"]


def test_a_turn_with_no_artifact_change_commits_nothing(monkeypatch, runner):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success(response="Just a question."))
    runner.run_turn("Build a habit tracker.")

    from agentic_dev.adapters.git.artifacts import log
    assert log(runner.store.artifacts_dir()) == []


def test_a_failed_turn_does_not_commit(monkeypatch, runner):
    def fake_run_that_writes_then_fails(request, usage_file=None):
        runner.store.artifact("prd.md").parent.mkdir(parents=True, exist_ok=True)
        runner.store.artifact("prd.md").write_text("half-written", encoding="utf-8")
        return HermesResult(
            response="", usage={"failed": True, "failure": "boom"}, exit_code=1, session_id=None,
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run_that_writes_then_fails)
    runner.run_turn("Build a habit tracker.")

    from agentic_dev.adapters.git.artifacts import log
    assert log(runner.store.artifacts_dir()) == []


# ---- codebase index wiring (indexing/, Phase 4) -----------------------------


class FakeIndexer:
    """Standing in for DartAnalyzerIndexer -- these tests need no Dart SDK."""

    def supports(self, project_root):
        return True

    def build(self, project_root):
        from agentic_dev.ports.indexer import IndexResult
        return IndexResult(nodes=(), edges=())


def _git_repo(path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.dart").write_text("class A {}", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=path, check=True)


def test_no_indexer_configured_means_no_index_revision(monkeypatch, runner):
    runner.store.update_state(workflow_state="implementation", attempts=0)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    runner.run_turn("build it")

    assert runner.store.read_state()["index_revision"] is None


def test_guided_role_with_an_indexer_gets_a_real_index_revision(monkeypatch, runner, tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    _git_repo(project)

    runner.project_source_root = project
    runner.indexer = FakeIndexer()
    runner.store.update_state(workflow_state="implementation", attempts=0)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    runner.run_turn("build it")

    from agentic_dev.adapters.indexing.freshness import current_git_revision
    assert runner.store.read_state()["index_revision"] == current_git_revision(project)


def test_assembled_role_never_gets_an_index_revision_even_with_an_indexer(monkeypatch, runner, tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    _git_repo(project)

    runner.project_source_root = project
    runner.indexer = FakeIndexer()
    # runner's default state is "planning" -- role: planner, context_mode: assembled
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    runner.run_turn("Build a habit tracker.")

    assert runner.store.read_state()["index_revision"] is None


def test_index_provider_items_appear_in_the_manifest_for_a_guided_turn(monkeypatch, runner, tmp_path):
    from datetime import datetime, timezone

    from agentic_dev.adapters.indexing.freshness import IndexMetadata, current_git_revision, save_graph, save_metadata
    from agentic_dev.ports.indexer import IndexNode, IndexResult

    project = tmp_path / "source"
    project.mkdir()
    _git_repo(project)
    # Pre-seed both the graph and its metadata, matching the current
    # revision, so ensure_fresh() treats this as already-cached and
    # doesn't overwrite it by calling FakeIndexer.build() (which returns
    # an empty graph).
    save_graph(runner.store.index_dir(), IndexResult(
        nodes=(IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),),
        edges=(),
    ))
    save_metadata(runner.store.index_dir(), IndexMetadata(
        tool="FakeIndexer", source_revision=current_git_revision(project),
        generated_at=datetime.now(timezone.utc).isoformat(),
    ))

    runner.project_source_root = project
    runner.indexer = FakeIndexer()
    runner.store.update_state(workflow_state="implementation", attempts=0)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    outcome = runner.run_turn("build it")

    manifest = runner.store.read_turn_artifact(outcome.turn_id, "manifest.yaml")
    assert "lib/a.dart" in manifest


# ---- project source git integration (Phase 5) -------------------------------


REVIEW_WORKFLOW_YAML = """
initial: implementation
states:
  implementation:
    kind: autonomous
    role: builder
    max_attempts: 2
    on_failure: blocked
    next: review
  review:
    kind: autonomous
    role: reviewer
    max_attempts: 2
    on_failure: blocked
    next: done
  done:
    kind: terminal
  blocked:
    kind: terminal
    recoverable: true
"""

REVIEW_AGENTS_YAML = """
defaults:
  toolsets: [skills]
  context_mode: assembled
roles:
  builder:
    context_mode: guided
    toolsets: [terminal]
    budget: {max_input_tokens: 20000, max_output_tokens: 6000}
    read_budget: {max_files: 10, max_bytes: 10000, max_tool_calls: 20}
  reviewer:
    context_mode: guided
    toolsets: [terminal]
    budget: {max_input_tokens: 20000, max_output_tokens: 6000}
    read_budget: {max_files: 10, max_bytes: 10000, max_tool_calls: 20}
"""

REVIEW_MODELS_YAML = """
routing:
  builder: {provider: openrouter, model: openai/gpt-4o-mini}
  reviewer: {provider: openrouter, model: openai/gpt-4o-mini}
"""


@pytest.fixture
def review_runner(tmp_path):
    (tmp_path / "workflow.yaml").write_text(REVIEW_WORKFLOW_YAML)
    (tmp_path / "agents.yaml").write_text(REVIEW_AGENTS_YAML)
    (tmp_path / "models.yaml").write_text(REVIEW_MODELS_YAML)
    (tmp_path / "souls").mkdir()

    store = ProjectStore(tmp_path / "agent-state")
    workflow = WorkflowDefinition.load(tmp_path / "workflow.yaml")
    agents = AgentsConfig.load(tmp_path / "agents.yaml")
    models = ModelsConfig.load(tmp_path / "models.yaml", env={})

    project = tmp_path / "source"
    project.mkdir()
    _git_repo(project)

    return TurnRunner(
        project_id="toy", store=store, workflow=workflow, agents=agents,
        models=models, souls_dir=str(tmp_path / "souls"),
        project_source_root=project,
    )


def test_implementation_state_captures_a_base_revision_on_first_turn(monkeypatch, review_runner):
    from agentic_dev.adapters.git.project import current_revision

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    review_runner.run_turn("build it")

    assert review_runner.store.read_state()["implementation_base_revision"] == (
        current_revision(review_runner.project_source_root)
    )


def test_builder_turn_commits_project_source_changes(monkeypatch, review_runner):
    from agentic_dev.adapters.git.project import current_revision

    base_revision = current_revision(review_runner.project_source_root)

    def fake_run_that_writes_a_file(request, usage_file=None):
        (review_runner.project_source_root / "new_file.dart").write_text("class New {}", encoding="utf-8")
        return HermesResult(
            response="Added a class.",
            usage={"failed": False, "session_id": "sess-1", "input_tokens": 100},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run_that_writes_a_file)

    review_runner.run_turn("build it")

    new_revision = current_revision(review_runner.project_source_root)
    assert new_revision != base_revision
    assert any(e["type"] == "SOURCE_COMMITTED" for e in review_runner.store.read_events())


def test_builder_turn_with_no_source_changes_commits_nothing(monkeypatch, review_runner):
    from agentic_dev.adapters.git.project import current_revision

    base_revision = current_revision(review_runner.project_source_root)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_success())

    review_runner.run_turn("build it")

    assert current_revision(review_runner.project_source_root) == base_revision


def test_base_revision_is_stable_across_a_second_implementation_attempt(monkeypatch, review_runner):
    from agentic_dev.adapters.git.project import current_revision

    def fake_run_that_writes_a_file(request, usage_file=None):
        (review_runner.project_source_root / "attempt.dart").write_text("class A {}", encoding="utf-8")
        return HermesResult(
            response="ok", usage={"failed": False, "session_id": "sess-1", "input_tokens": 10},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run_that_writes_a_file)
    review_runner.run_turn("build it")
    first_base = review_runner.store.read_state()["implementation_base_revision"]

    # Simulate a QA rejection sending the project back through implementation.
    review_runner.store.update_state(workflow_state="implementation", attempts=0)
    review_runner.run_turn("build it again")

    assert review_runner.store.read_state()["implementation_base_revision"] == first_base


def test_reviewer_gets_the_real_diff_after_a_builder_commit(monkeypatch, review_runner):
    def fake_run_that_writes_a_feature(request, usage_file=None):
        (review_runner.project_source_root / "feature.dart").write_text(
            "class Feature {}", encoding="utf-8",
        )
        return HermesResult(
            response="ok", usage={"failed": False, "session_id": "sess-1", "input_tokens": 10},
            exit_code=0, session_id="sess-1",
        )

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run_that_writes_a_feature)

    review_runner.run_turn("build it")
    review_runner.store.update_state(workflow_state="review", attempts=0)
    outcome = review_runner.run_turn("review it")

    manifest = review_runner.store.read_turn_artifact(outcome.turn_id, "manifest.yaml")
    assert "git-diff" in manifest
    prompt = review_runner.store.read_turn_artifact(outcome.turn_id, "prompt.md")
    assert "+class Feature {}" in prompt
