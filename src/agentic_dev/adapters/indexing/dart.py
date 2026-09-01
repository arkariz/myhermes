"""DartAnalyzerIndexer -- the CodebaseIndexer port implemented by shelling
out to tools/dart_indexer's Dart CLI (package:analyzer, AST-only) and
normalizing its JSON output into this package's own types.

Deliberately a subprocess boundary, not a Python reimplementation of Dart
parsing: package:analyzer is the same engine the Dart LSP uses, and no
Python library gets Dart syntax right the way the real Dart toolchain does.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ...settings import settings

from ...ports.indexer import IndexEdge, IndexNode, IndexResult


class DartIndexerError(Exception):
    """The dart_indexer CLI could not be run, or returned something that
    isn't the JSON graph it's supposed to produce."""


class DartAnalyzerIndexer:
    def __init__(self, dart_executable: str | None = None):
        # No universal default path for `dart` -- fvm, the Flutter SDK's
        # own bin/, and a system install all put it somewhere different.
        # DART_EXECUTABLE lets a deployment say once; plain "dart" is the
        # reasonable fallback when it's already on PATH.
        #
        # Confirmed live on Windows: point this at `dart.bat`, not the
        # extension-less `dart` shim -- subprocess.run's CreateProcess
        # can't exec that one directly ("%1 is not a valid Win32
        # application"), even though a shell invocation of bare `dart`
        # resolves and runs it fine via PATHEXT.
        self.dart_executable = dart_executable or os.environ.get("DART_EXECUTABLE", "dart")

    def supports(self, project_root: Path) -> bool:
        return (project_root / "pubspec.yaml").exists()

    def build(self, project_root: Path, *, timeout_seconds: float = 120.0) -> IndexResult:
        argv = [
            self.dart_executable, "run", "bin/dart_indexer.dart", str(project_root),
        ]
        try:
            result = subprocess.run(
                argv, cwd=settings.dart_indexer_dir, capture_output=True, text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DartIndexerError(f"failed to launch dart_indexer: {exc}") from exc

        warnings = tuple(
            line[len("warning: "):]
            for line in result.stderr.splitlines() if line.startswith("warning:")
        )

        if result.returncode != 0:
            raise DartIndexerError(
                f"dart_indexer exited {result.returncode}: {result.stderr.strip()}"
            )

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DartIndexerError(f"dart_indexer produced invalid JSON: {exc}") from exc

        nodes = tuple(IndexNode(**n) for n in data["nodes"])
        edges = tuple(IndexEdge(**e) for e in data["edges"])
        return IndexResult(nodes=nodes, edges=edges, warnings=warnings)
