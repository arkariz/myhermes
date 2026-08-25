"""RemoteIndexer -- the CodebaseIndexer port, over HTTP to agent-runtime's
POST /index, instead of shelling out to a local Dart CLI.

Exists for the containerized topology: the orchestrator container
deliberately has no Dart SDK (the heavy toolchain lives only in
agent-runtime, same reasoning as `runtime/client.py` for Hermes itself),
so `DartAnalyzerIndexer.build()` would fail to launch there even though
the `indexing` package itself is importable. `TurnRunner`/`IndexProvider`
hold a `CodebaseIndexer` and never know which concrete one they got --
`orchestrator/cli.py`/`telegram_bot/handlers.py` pick `RemoteIndexer` over
`DartAnalyzerIndexer` the same way they already pick `runtime.client.run`
over `runtime.hermes.run`: based on whether `AGENTIC_RUNTIME_URL` is set.

`project_root` crosses the HTTP boundary as a path, not file contents --
valid on both sides because both containers mount the same
`/workspace/projects` volume at the same path (`compose.yaml`), the same
pattern `runtime/client.py` already uses for `home_dir`/`usage_file`.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from .dart_adapter import DartIndexerError
from .port import IndexEdge, IndexNode, IndexResult


class RemoteIndexer:
    def __init__(self, *, base_url: str, timeout_seconds: float = 120.0):
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def supports(self, project_root: Path) -> bool:
        # A cheap local check, not a round trip: whether this LOOKS like a
        # Dart/Flutter project is knowable from the path alone, and
        # TurnRunner calls supports() far more often than build() (every
        # turn, to decide whether to bother indexing at all).
        return (project_root / "pubspec.yaml").exists()

    def build(self, project_root: Path) -> IndexResult:
        try:
            response = httpx.post(
                f"{self.base_url}/index", json={"project_root": str(project_root)},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise DartIndexerError(f"could not reach runtime server at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise DartIndexerError(f"runtime server returned {response.status_code}: {response.text}")

        data = response.json()
        nodes = tuple(IndexNode(**n) for n in data["nodes"])
        edges = tuple(IndexEdge(**e) for e in data["edges"])
        return IndexResult(nodes=nodes, edges=edges, warnings=tuple(data.get("warnings", ())))
