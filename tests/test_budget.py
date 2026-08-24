"""Budget selection (what gets in) and volatility ordering (where it sits) are
independent axes -- this file proves neither one silently governs the other,
and that a full rebuild is byte-identical given identical inputs."""

from orchestrator.context.budget import (
    ContextItem,
    dedupe,
    order_for_render,
    select_within_budget,
)


def item(key, priority, volatility, tokens, content="x", layer=3):
    return ContextItem(
        key=key, layer=layer, priority=priority, volatility=volatility,
        reason="test", tokens=tokens, content=content,
    )


def test_budget_keeps_highest_priority_first():
    items = [item("low", priority=9, volatility=5, tokens=50),
              item("high", priority=1, volatility=5, tokens=50)]
    result = select_within_budget(items, max_input_tokens=50)
    assert [i.key for i in result.selected] == ["high"]
    assert result.omitted[0][0] == "low"


def test_omission_reason_is_recorded():
    items = [item("a", 1, 1, 100)]
    result = select_within_budget(items, max_input_tokens=10)
    assert result.omitted[0][0] == "a"
    assert "budget exceeded" in result.omitted[0][1]


def test_lower_priority_item_can_still_fit_after_a_skip():
    # Priority governs order of consideration, not a hard veto once something
    # ahead of it didn't fit.
    items = [
        item("big", priority=1, volatility=1, tokens=100),
        item("small", priority=2, volatility=1, tokens=10),
    ]
    result = select_within_budget(items, max_input_tokens=10)
    assert [i.key for i in result.selected] == ["small"]


def test_ordering_is_independent_of_priority():
    # "high" has top priority (selected first) but is the most volatile
    # (rendered last) -- proving selection and placement don't collapse to
    # one axis.
    items = [item("high", priority=1, volatility=9, tokens=10),
              item("low", priority=9, volatility=1, tokens=10)]
    selected = select_within_budget(items, max_input_tokens=100).selected
    rendered = order_for_render(selected)
    assert [i.key for i in rendered] == ["low", "high"]


def test_render_order_is_deterministic_given_identical_inputs():
    items = [item("b", 1, 1, 10), item("a", 1, 1, 10)]
    first = [i.key for i in order_for_render(items)]
    second = [i.key for i in order_for_render(list(reversed(items)))]
    assert first == second == ["a", "b"]


def test_dedupe_drops_identical_content_keeping_higher_priority():
    items = [
        item("decision-1", priority=3, volatility=2, tokens=10, content="same text"),
        item("prd-quote", priority=5, volatility=3, tokens=10, content="same text"),
    ]
    kept, dropped = dedupe(items)
    assert [i.key for i in kept] == ["decision-1"]
    assert dropped[0][0] == "prd-quote"


def test_dedupe_drops_duplicate_keys():
    items = [item("a", 1, 1, 10), item("a", 2, 1, 10, content="different")]
    kept, dropped = dedupe(items)
    assert len(kept) == 1
    assert dropped[0][0] == "a"


def test_reference_items_are_never_deduped_by_content_hash():
    # References have content=None; two different paths must both survive.
    refs = [item("path/a.dart", 5, 4, 10, content=None),
             item("path/b.dart", 5, 4, 10, content=None)]
    kept, dropped = dedupe(refs)
    assert len(kept) == 2
    assert dropped == []
