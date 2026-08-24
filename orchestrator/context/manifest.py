"""The context manifest -- the record that makes a turn explainable.

Every turn must be able to answer: why was this included, why was that
omitted, which index revision was used, how many tokens were sent.

Two flavours, and the difference is honest rather than cosmetic:

  assembled  the builder pre-read documents, so the manifest is what we
             PREDICTED the agent needed.
  guided     the agent read with its own tools, so the manifest is rebuilt
             from the tool-call log -- what it ACTUALLY read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .budget import ContextItem, ReadBudget


@dataclass
class ContextPackage:
    prompt: str
    mode: str                                   # "assembled" | "guided"
    kind: str                                   # "full" | "delta"
    role: str
    workflow_state: str
    manifest: list[ContextItem] = field(default_factory=list)
    omitted: list[tuple[str, str]] = field(default_factory=list)
    estimated_tokens: int = 0
    resumed_from: str | None = None
    read_budget: ReadBudget | None = None
    source_revision: str | None = None
    index_revision: str | None = None

    @property
    def manifest_keys(self) -> list[str]:
        return [i.key for i in self.manifest]

    def to_yaml(self) -> str:
        """Serialise for turns/<id>/manifest.yaml."""
        doc: dict[str, Any] = {
            "mode": self.mode,
            "kind": self.kind,
            "role": self.role,
            "workflow_state": self.workflow_state,
            "estimated_tokens": self.estimated_tokens,
            "source_revision": self.source_revision,
            "index_revision": self.index_revision,
        }
        if self.resumed_from:
            doc["resumed_from"] = self.resumed_from
            doc["note"] = (
                "Continuation turn. This manifest lists the delta only; the "
                "resumed Hermes session also carries prior turns that are not "
                "described here."
            )
        if self.read_budget:
            doc["read_budget"] = {
                "max_files": self.read_budget.max_files,
                "max_bytes": self.read_budget.max_bytes,
                "max_tool_calls": self.read_budget.max_tool_calls,
            }
        doc["included"] = [
            {
                "key": i.key,
                "reason": i.reason,
                "layer": i.layer,
                "tokens": i.tokens,
                "kind": "reference" if i.is_reference else "content",
            }
            for i in self.manifest
        ]
        doc["omitted"] = [{"key": k, "reason": r} for k, r in self.omitted]
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def manifest_from_tool_log(entries: list[dict[str, Any]]) -> list[ContextItem]:
    """Reconstruct a guided-mode manifest from what the agent actually read.

    Accepts the tool-call log emitted by the runtime. Only read-shaped calls
    contribute; writes and builds are recorded elsewhere. Repeated reads of one
    path collapse to a single entry with the total observed size.
    """
    READ_TOOLS = {"file_read", "read_file", "cat", "rg", "grep", "git_diff", "git diff"}

    by_path: dict[str, ContextItem] = {}
    for i, entry in enumerate(entries):
        tool = str(entry.get("tool", "")).strip()
        if tool not in READ_TOOLS:
            continue
        path = entry.get("path") or entry.get("target") or entry.get("args", {}).get("path")
        if not path:
            continue
        path = str(path)
        tokens = int(entry.get("tokens") or 0)
        if path in by_path:
            by_path[path].tokens += tokens
            continue
        by_path[path] = ContextItem(
            key=path,
            layer=3,
            priority=5,
            volatility=4,
            reason=f"read by agent via {tool} (call #{i + 1})",
            tokens=tokens,
            content=None,
        )
    return list(by_path.values())
