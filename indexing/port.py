"""The CodebaseIndexer port (docs/plan.md's Phase 4 design).

A normalized schema so a context provider never needs to know which
concrete indexer produced a project's graph -- `DartAnalyzerIndexer` today,
a `GraphifyIndexer` for non-Dart repos later, both producing the same
`IndexResult` shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class IndexNode:
    id: str
    kind: str  # file | class | widget | mixin | enum | extension | function | method
    name: str
    file: str
    line: int
    module: str | None = None
    lang: str = "dart"


@dataclass(frozen=True)
class IndexEdge:
    src: str
    dst: str
    relation: str  # imports | declares | extends | implements | with
    confidence: float = 1.0


@dataclass(frozen=True)
class IndexResult:
    nodes: tuple[IndexNode, ...]
    edges: tuple[IndexEdge, ...]
    warnings: tuple[str, ...] = ()


class CodebaseIndexer(Protocol):
    def supports(self, project_root: Path) -> bool: ...
    def build(self, project_root: Path) -> IndexResult: ...
