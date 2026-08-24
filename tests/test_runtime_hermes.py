"""Unit tests for the Hermes invocation contract.

All of this is checked against ground truth captured live in the Phase 1
spike (docs/plan.md): --usage-file and --resume are NOT both available on
one invocation path (-z has no resume parameter at all; chat has no
--usage-file), so full turns use -z and continuation turns use
chat -q --resume + a post-hoc `sessions export` for usage. HERMES_HOME (not
HERMES_PROFILE) is the isolation boundary. No subprocess or network call
happens here -- `runner` is injected.
"""

from pathlib import Path

import pytest

from runtime.hermes import (
    HermesInvocationError,
    HermesRequest,
    build_argv,
    build_env,
    build_usage_export_argv,
    extract_session_id,
    parse_session_export,
    parse_usage_file,
    run,
)


def make_request(**overrides):
    defaults = dict(
        prompt="hello",
        home_dir=Path("/workspace/agent-state/toy/.hermes-home"),
        provider="openrouter",
        model="openai/gpt-4o-mini",
    )
    defaults.update(overrides)
    return HermesRequest(**defaults)


# ---- argv construction: full turns use -z -------------------------------


def test_full_turn_uses_top_level_z():
    # -z (hermes_cli/oneshot.py::run_oneshot) is the only path that honors
    # --usage-file -- confirmed by reading its call sites.
    argv = build_argv(make_request(), usage_file=Path("/tmp/usage.json"))
    assert argv[:2] == ["hermes", "-z"]
    assert "--usage-file" in argv


def test_full_turn_has_no_resume_flag():
    # -z's run_oneshot() signature has no resume parameter at all, and none
    # of its 3 call sites in main.py pass one -- -z --resume is dead code
    # even though argparse accepts the flag.
    argv = build_argv(make_request(), usage_file=Path("u.json"))
    assert "--resume" not in argv


def test_full_turn_usage_file_is_optional():
    argv = build_argv(make_request(), usage_file=None)
    assert "--usage-file" not in argv


def test_provider_and_model_are_always_explicit_on_full_turn():
    argv = build_argv(make_request(provider="openrouter", model="x/y"), usage_file=Path("u.json"))
    assert "--provider" in argv and "openrouter" in argv
    assert "--model" in argv and "x/y" in argv


# ---- argv construction: continuation turns use chat -q --resume ---------


def test_continuation_turn_uses_chat_q():
    # chat's own subparser has no --usage-file option at all (confirmed:
    # passing it is a hard argparse error, not a silent no-op) -- so
    # continuation turns never request one here.
    argv = build_argv(
        make_request(resume_session_id="20260824_150010_373368"),
        usage_file=Path("should-be-ignored.json"),
    )
    assert argv[:2] == ["hermes", "chat"]
    assert "--usage-file" not in argv


def test_continuation_turn_passes_resume_flag():
    argv = build_argv(
        make_request(resume_session_id="20260824_150010_373368"),
        usage_file=None,
    )
    assert "--resume" in argv
    assert "20260824_150010_373368" in argv


def test_provider_and_model_are_always_explicit_on_continuation_turn():
    argv = build_argv(
        make_request(resume_session_id="sid-1", provider="openrouter", model="x/y"),
        usage_file=None,
    )
    assert "--provider" in argv and "openrouter" in argv
    assert "--model" in argv and "x/y" in argv


def test_usage_export_argv_targets_the_right_session():
    argv = build_usage_export_argv("sid-42")
    assert argv[:3] == ["hermes", "sessions", "export"]
    assert "--session-id" in argv and "sid-42" in argv
    assert "jsonl" in argv


# ---- toolsets/skills apply to either path --------------------------------


def test_toolsets_and_skills_are_optional():
    bare = build_argv(make_request(), usage_file=Path("u.json"))
    assert "--toolsets" not in bare and "--skills" not in bare

    full = build_argv(
        make_request(toolsets="terminal,files", skills="flutter"),
        usage_file=Path("u.json"),
    )
    assert "--toolsets" in full and "terminal,files" in full
    assert "--skills" in full and "flutter" in full


# ---- environment: the isolation boundary -------------------------------


def test_hermes_home_is_always_set():
    # str(Path(...)) uses the host's native separator (backslash on Windows
    # dev, forward slash inside the Linux container) -- compare via Path
    # equality so this test is platform-independent.
    req = make_request(home_dir=Path("/workspace/agent-state/bonked/.hermes-home"))
    env = build_env(req)
    assert Path(env["HERMES_HOME"]) == Path("/workspace/agent-state/bonked/.hermes-home")


def test_two_projects_get_different_hermes_home():
    env_a = build_env(make_request(home_dir=Path("/a/.hermes-home")))
    env_b = build_env(make_request(home_dir=Path("/b/.hermes-home")))
    assert env_a["HERMES_HOME"] != env_b["HERMES_HOME"]


def test_extra_env_is_merged_without_dropping_hermes_home():
    req = make_request(extra_env={"OPENROUTER_API_KEY": "sk-test"})
    env = build_env(req)
    assert env["HERMES_HOME"]
    assert env["OPENROUTER_API_KEY"] == "sk-test"


# ---- --usage-file parsing (full turns) -----------------------------------


def test_missing_usage_file_is_treated_as_failed(tmp_path):
    usage = parse_usage_file(tmp_path / "does-not-exist.json")
    assert usage["failed"] is True


def test_malformed_usage_file_does_not_crash(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{not valid json", encoding="utf-8")
    usage = parse_usage_file(path)
    assert usage["failed"] is True


def test_valid_usage_file_round_trips(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text('{"input_tokens": 100, "session_id": "abc", "failed": false}', encoding="utf-8")
    usage = parse_usage_file(path)
    assert usage["input_tokens"] == 100
    assert usage["failed"] is False


# ---- sessions export parsing (continuation turns) ------------------------


def test_session_export_extracts_accounting_fields():
    line = (
        '{"id": "sid-1", "input_tokens": 228, "output_tokens": 32, '
        '"cache_read_tokens": 40192, "estimated_cost_usd": 0.0015339, '
        '"cost_status": "estimated", "model": "openai/gpt-4o-mini"}'
    )
    usage = parse_session_export(line)
    assert usage["input_tokens"] == 228
    assert usage["cache_read_tokens"] == 40192
    assert usage["session_id"] == "sid-1"
    assert usage["failed"] is False


def test_session_export_takes_the_last_line_of_jsonl():
    # `sessions export` is JSONL; be liberal and take the last record in
    # case of incidental leading blank lines or banner noise.
    text = "\n{\"id\": \"sid-1\", \"input_tokens\": 1}\n"
    usage = parse_session_export(text)
    assert usage["session_id"] == "sid-1"


def test_empty_session_export_is_treated_as_failed():
    usage = parse_session_export("")
    assert usage["failed"] is True


def test_malformed_session_export_does_not_crash():
    usage = parse_session_export("not json at all")
    assert usage["failed"] is True


# ---- session id recovery -------------------------------------------------


def test_session_id_prefers_usage_dict():
    usage = {"session_id": "from-usage"}
    assert extract_session_id("session_id: from-stdout\n\nhello", usage) == "from-usage"


def test_session_id_falls_back_to_stdout_line():
    usage = {"session_id": None}
    assert extract_session_id("session_id: from-stdout\n\nhello", usage) == "from-stdout"


def test_session_id_is_none_when_nowhere_to_be_found():
    assert extract_session_id("just a plain response", {}) is None


# ---- full run() with an injected fake subprocess ------------------------


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_run_full_turn_response_is_raw_stdout(tmp_path):
    # -z's stdout is the response and nothing else, by the CLI's own design
    # ("nothing else on stdout") -- confirmed live (a clean "OK." with no
    # session_id line, no banner). No cleaning needed or applied.
    usage_file = tmp_path / "usage.json"
    usage_file.write_text('{"session_id": "sid-1", "input_tokens": 50, "failed": false}')

    def fake_runner(argv, **kwargs):
        return FakeCompletedProcess(stdout="The actual response.")

    result = run(
        make_request(home_dir=tmp_path / ".hermes-home"),
        usage_file=usage_file,
        runner=fake_runner,
    )
    assert result.response == "The actual response."
    assert result.session_id == "sid-1"
    assert result.failed is False


def test_run_continuation_turn_strips_banner_noise(tmp_path):
    # Regression, confirmed live: on `chat -q --resume`, "session_id:" goes
    # to STDERR, not stdout -- unlike -z, there is no structural marker on
    # stdout separating a banner line (e.g. a security-scanner notice) from
    # the real response, so an earlier version that looked for that marker
    # returned the whole raw block, banner included. Filtering by known
    # status-icon prefixes is what actually works against real output.
    def fake_runner(argv, **kwargs):
        return FakeCompletedProcess(
            stdout="  ⚠ tirith security scanner enabled but not available"
                   " — command scanning will use pattern matching only\n"
                   "The real response."
        )

    result = run(
        make_request(home_dir=tmp_path / ".hermes-home", resume_session_id="sid-1"),
        usage_file=None,
        runner=fake_runner,
    )
    assert result.response == "The real response."
    assert "scanner" not in result.response


def test_run_continuation_turn_recovers_usage_via_sessions_export(tmp_path):
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "chat":
            return FakeCompletedProcess(stdout="Resumed response.")
        assert argv[1:3] == ["sessions", "export"]
        return FakeCompletedProcess(
            stdout='{"id": "sid-1", "input_tokens": 10, "output_tokens": 5}'
        )

    result = run(
        make_request(home_dir=tmp_path / ".hermes-home", resume_session_id="sid-1"),
        usage_file=None,
        runner=fake_runner,
    )
    assert result.response == "Resumed response."
    assert result.usage["input_tokens"] == 10
    # Continuation turns never extract a session id from output -- the id
    # being resumed is already known input.
    assert result.session_id == "sid-1"
    assert result.session_id == "sid-1"
    assert len(calls) == 2  # chat, then the usage export


def test_run_marks_failed_on_nonzero_exit_and_failed_usage(tmp_path):
    usage_file = tmp_path / "usage.json"

    def fake_runner(argv, **kwargs):
        return FakeCompletedProcess(stdout="", returncode=1)

    result = run(
        make_request(home_dir=tmp_path / ".hermes-home"),
        usage_file=usage_file,
        runner=fake_runner,
    )
    assert result.failed is True


def test_run_creates_home_dir_before_launching(tmp_path):
    home = tmp_path / "does" / "not" / "exist" / ".hermes-home"
    usage_file = tmp_path / "usage.json"
    usage_file.write_text('{"failed": false, "session_id": "x"}')

    def fake_runner(argv, **kwargs):
        assert home.is_dir()  # created before the subprocess launches
        return FakeCompletedProcess(stdout="ok")

    run(make_request(home_dir=home), usage_file=usage_file, runner=fake_runner)


def test_launch_failure_raises_invocation_error(tmp_path):
    def fake_runner(argv, **kwargs):
        raise FileNotFoundError("hermes: not found")

    with pytest.raises(HermesInvocationError):
        run(
            make_request(home_dir=tmp_path / ".hermes-home"),
            usage_file=tmp_path / "usage.json",
            runner=fake_runner,
        )
