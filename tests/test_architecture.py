"""Guards the layering this refactor exists to establish: an AST walk of
`src/agentic_dev`, no third-party dependency, <100ms. Three checks:

1. Imports only ever point downward: domain(0) -> ports(1) -> adapters(2)
   -> app(3) -> entrypoints(4). A module may import its own layer or any
   strictly lower-ranked layer, never a higher one.
2. `domain/` stays pure: no `os` import anywhere under it (the module that
   needs "the environment" takes it as a parameter instead -- see
   `domain/roles.py::resolve_env_placeholders`).
3. No module outside `settings.py` evaluates `Path(__file__)` (or bare
   `__file__`) to locate anything -- that's `settings.py`'s one job,
   everything else gets handed a path.

One deliberate, documented exception to rule 1's letter: `adapters/` is a
single rank, and one adapter is allowed to import a sibling adapter --
concretely, `adapters/context/providers.py` composes `adapters/storage`,
`adapters/indexing`, and `adapters/git` to assemble a context package from
several sources on disk. That's still same-rank (2 -> 2), so rule 1 as
written already allows it; this docstring just names it so nobody mistakes
it for an oversight when they go looking for a stricter "adapters may never
import each other" rule the plan's prose gestures at but rule 1's actual
downward-or-same-rank shape does not, and does not need to, enforce.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "agentic_dev"

LAYER_RANK = {
    "domain": 0,
    "ports": 1,
    "adapters": 2,
    "app": 3,
    "entrypoints": 4,
}


def _all_modules() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _module_layer(path: Path) -> str | None:
    rel = path.relative_to(SRC_ROOT)
    if len(rel.parts) < 2:
        return None  # settings.py, __init__.py -- not layer-owned
    return rel.parts[0]


def _imported_module_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import -- resolved separately in _resolve_relative.
                names.append(("relative", node.level, node.module))
            elif node.module:
                names.append(node.module)
    return names


def _resolve_relative(path: Path, level: int, module: str | None) -> str | None:
    """`from ..domain.roles import X` inside adapters/context/providers.py
    -> "agentic_dev.domain.roles". Mirrors Python's own relative-import
    resolution against the importing file's package, not against SRC_ROOT."""
    package_parts = path.relative_to(SRC_ROOT.parent).parts[:-1]  # drop filename
    base = package_parts[: len(package_parts) - (level - 1)] if level > 1 else package_parts
    if module:
        return ".".join((*base, module))
    return ".".join(base) if base else None


def _target_layer(dotted: str) -> str | None:
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != "agentic_dev":
        return None
    if parts[1] not in LAYER_RANK:
        return None
    return parts[1]


def test_imports_never_point_upward():
    violations = []
    for path in _all_modules():
        importer_layer = _module_layer(path)
        if importer_layer is None or importer_layer not in LAYER_RANK:
            continue
        importer_rank = LAYER_RANK[importer_layer]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for raw in _imported_module_names(tree):
            if isinstance(raw, tuple):
                _, level, module = raw
                dotted = _resolve_relative(path, level, module)
            else:
                dotted = raw
            if dotted is None:
                continue
            target_layer = _target_layer(dotted)
            if target_layer is None:
                continue
            target_rank = LAYER_RANK[target_layer]
            if target_rank > importer_rank:
                violations.append(
                    f"{path.relative_to(SRC_ROOT.parent)}: {importer_layer}(rank "
                    f"{importer_rank}) imports {dotted} -- {target_layer}(rank "
                    f"{target_rank}), which is upward"
                )

    assert violations == [], "Upward imports found:\n" + "\n".join(violations)


def test_domain_never_imports_os():
    violations = []
    domain_dir = SRC_ROOT / "domain"
    for path in sorted(domain_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "os" for a in node.names):
                violations.append(f"{path.relative_to(SRC_ROOT.parent)}: imports os")
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                violations.append(f"{path.relative_to(SRC_ROOT.parent)}: imports from os")

    assert violations == [], "domain/ must stay pure:\n" + "\n".join(violations)


def test_only_settings_locates_itself_via_dunder_file():
    violations = []
    for path in _all_modules():
        if path.name == "settings.py" and path.parent == SRC_ROOT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__file__":
                violations.append(f"{path.relative_to(SRC_ROOT.parent)}: uses __file__")

    assert violations == [], (
        "Only settings.py may use __file__ to locate anything:\n" + "\n".join(violations)
    )
