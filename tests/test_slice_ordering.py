"""Tests for the pure-function slice-ordering algorithm in
`labdash/_slice_order.py`. The same algorithm is mirrored in JS in
`labdash/templates/index.html`; this test suite validates the
specification both implementations share.
"""
from labdash._slice_order import Card, compute_new_order, resolve_range


# Convenience builders ------------------------------------------------------

def _cards(*specs: str) -> list[Card]:
    """Build a model from compact specs like 'A:g1' or 'A:g1:hidden'."""
    out = []
    for spec in specs:
        parts = spec.split(":")
        slug = parts[0]
        group = parts[1] if len(parts) > 1 else "g1"
        visible = not (len(parts) > 2 and parts[2] == "hidden")
        out.append(Card(slug=slug, group=group, visible=visible))
    return out


# compute_new_order: single-card moves -------------------------------------

def test_single_card_top_when_already_top_is_noop():
    model = _cards("A", "B", "C")
    assert compute_new_order({"A"}, "top", model) == ["A", "B", "C"]


def test_single_card_top_moves_mid_to_front():
    model = _cards("A", "B", "C")
    assert compute_new_order({"B"}, "top", model) == ["B", "A", "C"]


def test_single_card_bottom_when_already_bottom_is_noop():
    model = _cards("A", "B", "C")
    assert compute_new_order({"C"}, "bottom", model) == ["A", "B", "C"]


def test_single_card_bottom_moves_mid_to_end():
    model = _cards("A", "B", "C")
    assert compute_new_order({"B"}, "bottom", model) == ["A", "C", "B"]


def test_single_card_up_swaps_with_previous():
    model = _cards("A", "B", "C")
    assert compute_new_order({"B"}, "up", model) == ["B", "A", "C"]


def test_single_card_up_at_top_is_noop():
    model = _cards("A", "B", "C")
    assert compute_new_order({"A"}, "up", model) == ["A", "B", "C"]


def test_single_card_down_swaps_with_next():
    model = _cards("A", "B", "C")
    assert compute_new_order({"B"}, "down", model) == ["A", "C", "B"]


def test_single_card_down_at_bottom_is_noop():
    model = _cards("A", "B", "C")
    assert compute_new_order({"C"}, "down", model) == ["A", "B", "C"]


# compute_new_order: cross-group selection ---------------------------------

def test_multi_card_across_two_groups_each_moves_within_own_group():
    model = _cards("A:g1", "B:g1", "C:g1", "D:g2", "E:g2", "F:g2")
    # Move B (g1) and F (g2 — already at bottom) to bottom
    assert compute_new_order({"B", "F"}, "bottom", model) == [
        "A", "C", "B", "D", "E", "F",
    ]


def test_multi_card_top_keeps_relative_order():
    model = _cards("A", "B", "C", "D", "E")
    # Selecting C and E, move to top — preserves their relative order
    assert compute_new_order({"C", "E"}, "top", model) == ["C", "E", "A", "B", "D"]


def test_multi_card_bottom_keeps_relative_order():
    model = _cards("A", "B", "C", "D", "E")
    assert compute_new_order({"A", "C"}, "bottom", model) == ["B", "D", "E", "A", "C"]


def test_multi_card_up_non_contiguous_each_shifts_one_slot():
    # Selected = {A, C}: A is at top so can't move; C swaps with B.
    model = _cards("A", "B", "C", "D")
    assert compute_new_order({"A", "C"}, "up", model) == ["A", "C", "B", "D"]


def test_multi_card_up_contiguous_at_top_is_noop():
    model = _cards("A", "B", "C", "D")
    assert compute_new_order({"A", "B"}, "up", model) == ["A", "B", "C", "D"]


def test_multi_card_down_contiguous_at_bottom_is_noop():
    model = _cards("A", "B", "C", "D")
    assert compute_new_order({"C", "D"}, "down", model) == ["A", "B", "C", "D"]


# compute_new_order: filtered (hidden) cards -------------------------------

def test_hidden_cards_keep_their_slots_during_visible_reorder():
    # Cards: A, B(hidden), C, D — slice is [A, C, D]. Move D up.
    model = _cards("A", "B:g1:hidden", "C", "D")
    # D up in the visible slice → swap D and C → visible new order [A, C, D] → wait
    # Let me think: slice = [A, C, D]. Up on D: swap with C. New slice: [A, D, C].
    # Stitched: A at visible-slot 0 stays A; B(hidden) stays; visible-slot 1 → D; visible-slot 2 → C.
    assert compute_new_order({"D"}, "up", model) == ["A", "B", "D", "C"]


def test_hidden_selected_card_is_excluded_from_moves():
    # Selected = {B} but B is hidden — slice is [A, C, D] with no selection in it.
    model = _cards("A", "B:g1:hidden", "C", "D")
    assert compute_new_order({"B"}, "top", model) == ["A", "B", "C", "D"]


def test_filtered_top_moves_to_top_of_visible_slice_not_absolute_top():
    # A is hidden at index 0. Visible slice is [B, C, D]. Move D to top → [D, B, C].
    # Stitched: hidden A stays at index 0; visible slots fill with [D, B, C].
    model = _cards("A:g1:hidden", "B", "C", "D")
    assert compute_new_order({"D"}, "top", model) == ["A", "D", "B", "C"]


# resolve_range -----------------------------------------------------------

def test_resolve_range_simple_forward():
    model = _cards("A", "B", "C", "D", "E")
    assert resolve_range("B", "D", model) == ["B", "C", "D"]


def test_resolve_range_reverse_endpoints_normalize():
    model = _cards("A", "B", "C", "D", "E")
    assert resolve_range("D", "B", model) == ["B", "C", "D"]


def test_resolve_range_with_hidden_in_middle_excludes_hidden():
    model = _cards("A", "B", "C:g1:hidden", "D", "E")
    assert resolve_range("A", "E", model) == ["A", "B", "D", "E"]


def test_resolve_range_no_anchor_returns_target_only():
    model = _cards("A", "B", "C")
    assert resolve_range(None, "B", model) == ["B"]


def test_resolve_range_anchor_hidden_falls_back_to_target_only():
    model = _cards("A:g1:hidden", "B", "C")
    assert resolve_range("A", "C", model) == ["C"]


def test_resolve_range_target_hidden_returns_empty():
    model = _cards("A", "B:g1:hidden", "C")
    assert resolve_range("A", "B", model) == []


def test_resolve_range_anchor_equals_target_returns_single():
    model = _cards("A", "B", "C")
    assert resolve_range("B", "B", model) == ["B"]
