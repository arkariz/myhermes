import sys

from runtime.rtk import compress, run


def test_ratio_is_zero_for_empty_text():
    result = compress("")
    assert result.ratio == 0.0
    assert result.text == ""


def test_short_output_with_no_repeats_is_left_unchanged():
    text = "line one\nline two\nline three"
    result = compress(text)
    assert result.text == text
    assert result.ratio == 0.0


def test_ansi_escape_codes_are_stripped():
    text = "\x1b[32mok\x1b[0m\n\x1b[31mfail\x1b[0m"
    result = compress(text)
    assert "\x1b" not in result.text
    assert "ok" in result.text and "fail" in result.text


def test_consecutive_repeated_lines_collapse_with_a_count():
    text = "\n".join(["same line"] * 50)
    result = compress(text)
    assert result.text == "same line  [x50]"
    assert result.ratio > 0.9


def test_two_repeats_do_not_collapse():
    text = "same line\nsame line"
    result = compress(text)
    assert result.text == text


def test_a_long_unbroken_run_is_truncated_in_the_middle():
    lines = [f"unique line {i}" for i in range(200)]
    result = compress("\n".join(lines))
    out_lines = result.text.splitlines()
    assert out_lines[0] == "unique line 0"
    assert out_lines[-1] == "unique line 199"
    assert "omitted" in out_lines[20]
    assert result.ratio > 0.5


def test_run_wraps_a_real_subprocess_and_compresses_its_repetitive_output():
    result = run(
        [sys.executable, "-c", "for i in range(500): print('build: OK')"],
        tool="fake-build-tool",
    )
    assert result.returncode == 0
    assert result.compression.tool == "fake-build-tool"
    assert "[x500]" in result.compression.text
    assert result.compression.ratio > 0.9


def test_run_reports_the_real_return_code_of_a_failing_command():
    result = run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.returncode == 3
