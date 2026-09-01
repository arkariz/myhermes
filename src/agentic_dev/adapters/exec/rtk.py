"""RTK -- tool-output compression (docs/plan.md's "Rust Token Killer" idea).

The real RTK referenced in the brief is an external Rust binary this project
doesn't vendor or depend on. This module does the same job in Python:
shrink verbose tool output (git, test runners, build tools, grep) before it
re-enters context, and record a *measured* compression ratio rather than
assume one -- the plan is explicit that RTK should be dropped if it doesn't
pay off, which only a real number can decide.

Nothing calls this yet. Phase 6 (`POST /exec`, RTK-wrapped `flutter test` /
`analyze` / `build`) is what will actually exercise it end to end; until
that toolchain exists there's no real tool output flowing through the
system to wrap. This ships the compressor itself, ready to be called from
there, verified against real subprocess output rather than fixtures.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# A run shorter than this is left alone -- collapsing a 10-line block saves
# nothing worth the risk of hiding a short, meaningful error.
_LONG_RUN_THRESHOLD = 60
_LONG_RUN_KEEP = 20

# Fewer repeats than this stays as-is -- two duplicate lines are cheap to
# read and cheaper to keep literal than to annotate.
_REPEAT_THRESHOLD = 3


@dataclass(frozen=True)
class CompressionResult:
    tool: str
    original_bytes: int
    compressed_bytes: int
    text: str

    @property
    def ratio(self) -> float:
        """Fraction of bytes removed. 0.0 when there was nothing to compress
        (never negative -- compression here can only shrink or leave text
        unchanged, it never expands it)."""
        if self.original_bytes == 0:
            return 0.0
        return 1 - (self.compressed_bytes / self.original_bytes)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _collapse_repeated_lines(lines: list[str]) -> list[str]:
    """Consecutive identical lines collapse to one plus a repeat count.

    Real tool output repeats itself constantly this way: a stalled
    progress spinner, a retry loop, the same lint warning printed once per
    file. One line carries the same information as a hundred.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i
        while j < len(lines) and lines[j] == line:
            j += 1
        count = j - i
        if count >= _REPEAT_THRESHOLD:
            out.append(f"{line}  [x{count}]")
        else:
            out.extend(lines[i:j])
        i = j
    return out


def _collapse_long_run(
    lines: list[str], *, keep: int = _LONG_RUN_KEEP, threshold: int = _LONG_RUN_THRESHOLD,
) -> list[str]:
    """A single unbroken block longer than `threshold` lines is truncated to
    its first and last `keep` lines. The middle of a giant stack dump or
    dependency-resolution log rarely carries information the two ends
    don't already summarize."""
    if len(lines) <= threshold:
        return lines
    omitted = len(lines) - 2 * keep
    return lines[:keep] + [f"... ({omitted} lines omitted) ..."] + lines[-keep:]


def compress(text: str, *, tool: str = "generic") -> CompressionResult:
    original_bytes = len(text.encode("utf-8"))
    lines = _strip_ansi(text).splitlines()
    lines = _collapse_repeated_lines(lines)
    lines = _collapse_long_run(lines)
    compressed_text = "\n".join(lines)
    return CompressionResult(
        tool=tool,
        original_bytes=original_bytes,
        compressed_bytes=len(compressed_text.encode("utf-8")),
        text=compressed_text,
    )


@dataclass(frozen=True)
class RtkRunResult:
    returncode: int
    compression: CompressionResult


def run(
    argv: list[str], *, tool: str | None = None,
    cwd: Path | str | None = None, timeout_seconds: float = 300.0,
) -> RtkRunResult:
    """Run a real subprocess and compress its combined output.

    Stdout and stderr are merged before compression: a build tool
    interleaves them meaningfully (a compiler error on stderr right after
    the file it belongs to on stdout), and splitting them apart loses that
    ordering for no benefit.
    """
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds,
    )
    combined = result.stdout + result.stderr
    compression = compress(combined, tool=tool or argv[0])
    return RtkRunResult(returncode=result.returncode, compression=compression)
