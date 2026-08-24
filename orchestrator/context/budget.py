"""Budget enforcement and the two ordering axes.

These were conflated in an earlier design; separating them is what makes the
package both bounded and cache-friendly:

  priority   -- WHAT gets in.  Lower wins when the budget is tight (brief 17.2).
  volatility -- WHERE it sits. Lower is more stable, so it renders earlier and
                the provider's prefix cache can hit across turns.

Selecting by priority but rendering by volatility means the stable prefix
(role soul, project identity) stays byte-identical between turns within a
state, while the parts that change every turn land at the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class ContextItem:
    key: str                    # "artifacts/prd.md#acceptance-criteria"
    layer: int                  # 1 stable | 2 workflow | 3 task | 4 recent
    priority: int               # selection order, lower kept first
    volatility: int             # placement order, lower rendered first
    reason: str                 # why this was selected, for the manifest
    tokens: int = 0
    content: str | None = None  # None in guided mode: a path is handed over
    section: str | None = None

    @property
    def is_reference(self) -> bool:
        """A path handed to the agent rather than embedded content."""
        return self.content is None


@dataclass
class ReadBudget:
    """Caps for guided-retrieval roles, which read with their own tools."""

    max_files: int = 40
    max_bytes: int = 400_000
    max_tool_calls: int = 120


@dataclass
class BudgetResult:
    selected: list[ContextItem]
    omitted: list[tuple[str, str]] = field(default_factory=list)
    total_tokens: int = 0


def select_within_budget(
    items: Iterable[ContextItem],
    *,
    max_input_tokens: int,
    reserve_tokens: int = 0,
) -> BudgetResult:
    """Greedily keep items by priority until the budget is spent.

    Every dropped item is recorded with a reason. "Why was this omitted" has to
    be answerable, so silent truncation is not an option.

    A lower-priority item that happens to fit after a larger one was skipped is
    still admitted -- refusing it would waste budget for no benefit, and the
    manifest records exactly what happened either way.
    """
    ordered = sorted(items, key=lambda i: (i.priority, i.key))
    limit = max(0, max_input_tokens - reserve_tokens)

    selected: list[ContextItem] = []
    omitted: list[tuple[str, str]] = []
    used = 0

    for item in ordered:
        if used + item.tokens <= limit:
            selected.append(item)
            used += item.tokens
        else:
            omitted.append(
                (
                    item.key,
                    f"budget exceeded: needs {item.tokens} tokens, "
                    f"{limit - used} of {limit} remaining",
                )
            )

    return BudgetResult(selected=selected, omitted=omitted, total_tokens=used)


def order_for_render(items: Iterable[ContextItem]) -> list[ContextItem]:
    """Order selected items for rendering: stable content first.

    The key includes `key` as a tiebreaker so the output is deterministic --
    a byte-identical prompt for identical inputs is what makes both prefix
    caching and reproducibility work.
    """
    return sorted(items, key=lambda i: (i.volatility, i.layer, i.priority, i.key))


def dedupe(items: Iterable[ContextItem]) -> tuple[list[ContextItem], list[tuple[str, str]]]:
    """Drop items whose content already appears in the package.

    The same decision can legitimately be surfaced by two providers -- once as
    a decision record, once quoted inside an artifact section. Paying for it
    twice is waste, so the first occurrence by priority wins.
    """
    seen_content: dict[int, str] = {}
    seen_keys: set[str] = set()
    kept: list[ContextItem] = []
    dropped: list[tuple[str, str]] = []

    for item in sorted(items, key=lambda i: (i.priority, i.key)):
        if item.key in seen_keys:
            dropped.append((item.key, "duplicate key"))
            continue
        seen_keys.add(item.key)

        if item.content is not None:
            h = hash(item.content.strip())
            if h in seen_content:
                dropped.append(
                    (item.key, f"identical content already included as {seen_content[h]}")
                )
                continue
            seen_content[h] = item.key

        kept.append(item)

    return kept, dropped
