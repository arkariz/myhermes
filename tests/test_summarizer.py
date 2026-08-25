"""orchestrator.summarizer -- pure logic (prompt shape) plus update_summary's
read-call-persist wiring, all with hermes_run monkeypatched. No subprocess,
no network call, no cost -- same discipline as every other hermes_run caller
in this codebase.
"""

from __future__ import annotations

import runtime.agent_runtime as agent_runtime_module
from orchestrator.config import ModelRoute
from orchestrator.store import ProjectStore
from orchestrator.summarizer import build_prompt, summarize, update_summary
from runtime.agent_runtime import HermesResult, HttpAgentRuntime, InProcessHermesRuntime


def test_build_prompt_includes_previous_summary_and_new_turn():
    prompt = build_prompt("Decided X.", "What about Y?", "Y is out of scope for v1.")
    assert "Decided X." in prompt
    assert "What about Y?" in prompt
    assert "Y is out of scope for v1." in prompt


def test_build_prompt_labels_a_first_turn_explicitly():
    prompt = build_prompt("", "Build a habit tracker.", "Sure, scoping now.")
    assert "none yet" in prompt.lower()


def test_summarize_returns_the_response_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: HermesResult(
        response="Updated summary.", usage={"failed": False}, exit_code=0, session_id=None,
    ))
    result = summarize(
        previous_summary="", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=InProcessHermesRuntime(),
    )
    assert result.failed is False
    assert result.summary == "Updated summary."


def test_summarize_never_resumes_a_session(monkeypatch, tmp_path):
    captured = {}

    def fake_run(request, usage_file=None):
        captured["resume_session_id"] = request.resume_session_id
        return HermesResult(response="ok", usage={"failed": False}, exit_code=0, session_id=None)

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    summarize(
        previous_summary="", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=InProcessHermesRuntime(),
    )
    assert captured["resume_session_id"] is None


def test_summarize_on_failure_keeps_the_previous_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: HermesResult(
        response="", usage={"failed": True}, exit_code=1, session_id=None,
    ))
    result = summarize(
        previous_summary="Decided X.", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=InProcessHermesRuntime(),
    )
    assert result.failed is True
    assert result.summary == "Decided X."


# ---- update_summary: read -> summarize -> persist --------------------------


def test_update_summary_persists_on_success(monkeypatch, tmp_path):
    store = ProjectStore(tmp_path)
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: HermesResult(
        response="New running summary.", usage={"failed": False}, exit_code=0, session_id=None,
    ))
    route = ModelRoute(provider="openrouter", model="deepseek/deepseek-v3")

    result = update_summary(
        store, "planning", route, human_message="hi", agent_response="hello",
        agent_runtime=InProcessHermesRuntime(),
    )

    assert result.failed is False
    assert store.read_summary("planning") == "New running summary."


def test_update_summary_folds_in_the_previous_summary(monkeypatch, tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("planning", "Decided X.")
    captured = {}

    def fake_run(request, usage_file=None):
        captured["prompt"] = request.prompt
        return HermesResult(response="Decided X and Y.", usage={"failed": False}, exit_code=0, session_id=None)

    monkeypatch.setattr(agent_runtime_module, "hermes_run", fake_run)
    route = ModelRoute(provider="openrouter", model="deepseek/deepseek-v3")

    update_summary(
        store, "planning", route, human_message="what about Y", agent_response="Y is in scope now",
        agent_runtime=InProcessHermesRuntime(),
    )

    assert "Decided X." in captured["prompt"]


def test_update_summary_does_not_persist_on_failure(monkeypatch, tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("planning", "Decided X.")
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: HermesResult(
        response="", usage={"failed": True}, exit_code=1, session_id=None,
    ))
    route = ModelRoute(provider="openrouter", model="deepseek/deepseek-v3")

    result = update_summary(
        store, "planning", route, human_message="hi", agent_response="hello",
        agent_runtime=InProcessHermesRuntime(),
    )

    assert result.failed is True
    assert store.read_summary("planning") == "Decided X."  # untouched


# ---- agent_runtime routing (container topology -- no Hermes CLI locally) ---
#
# summarize() takes the same AgentRuntime jobs.py's TurnRunner used for the
# turn itself, picked once at the entrypoint -- there is no separate
# runtime_url concept here any more (see runtime/agent_runtime.py).


def test_summarize_with_in_process_runtime_calls_hermes_run_directly(monkeypatch, tmp_path):
    called = {"direct": False, "client": False}
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: (
        called.__setitem__("direct", True) or
        HermesResult(response="ok", usage={"failed": False}, exit_code=0, session_id=None)
    ))
    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", lambda request, usage_file=None, base_url=None: (
        called.__setitem__("client", True) or
        HermesResult(response="via client", usage={"failed": False}, exit_code=0, session_id=None)
    ))

    result = summarize(
        previous_summary="", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=InProcessHermesRuntime(),
    )

    assert called == {"direct": True, "client": False}
    assert result.summary == "ok"


def test_summarize_with_an_http_runtime_calls_the_http_client_instead(monkeypatch, tmp_path):
    called = {"direct": False, "client": False}
    monkeypatch.setattr(agent_runtime_module, "hermes_run", lambda request, usage_file=None: (
        called.__setitem__("direct", True) or
        HermesResult(response="ok", usage={"failed": False}, exit_code=0, session_id=None)
    ))

    def fake_client_run(request, usage_file=None, base_url=None):
        called["client"] = True
        assert base_url == "http://agent-runtime:8000"
        return HermesResult(response="via client", usage={"failed": False}, exit_code=0, session_id=None)

    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", fake_client_run)

    result = summarize(
        previous_summary="", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=HttpAgentRuntime(base_url="http://agent-runtime:8000"),
    )

    assert called == {"direct": False, "client": True}
    assert result.summary == "via client"


def test_summarize_treats_a_runtime_client_error_as_a_best_effort_failure(monkeypatch, tmp_path):
    from runtime.client import RuntimeClientError

    def raise_client_error(request, usage_file=None, base_url=None):
        raise RuntimeClientError("could not reach runtime server")

    monkeypatch.setattr(agent_runtime_module, "runtime_client_run", raise_client_error)

    result = summarize(
        previous_summary="Decided X.", human_message="hi", agent_response="hello",
        home_dir=tmp_path, provider="openrouter", model="deepseek/deepseek-v3",
        agent_runtime=HttpAgentRuntime(base_url="http://agent-runtime:8000"),
    )

    assert result.failed is True
    assert result.summary == "Decided X."
