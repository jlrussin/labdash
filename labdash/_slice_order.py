"""Pure-function slice-ordering helpers, mirroring the JS in
`labdash/templates/index.html` (search for "Slice-aware ordering"). Keep
the two in sync — if you change one, change the other.

These compute the new DOM order produced by the per-card move buttons
(top / up / down / bottom) and resolve a shift-click range, against an
abstract model so the algorithm can be unit-tested without a browser.

A *model* is a list of `Card` records in current DOM order, each with:
- `slug` — unique id
- `group` — group name (cards in the same group must be contiguous in the model)
- `visible` — whether the card passes the current filter set
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Card:
    slug: str
    group: str
    visible: bool


def compute_new_order(
    selected_slugs: set[str] | list[str],
    action: str,
    model: list[Card],
) -> list[str]:
    """Return slugs in the new DOM order after applying `action` to selection.

    `action` is one of "top", "up", "down", "bottom". Cards in the same group
    stay contiguous; hidden cards keep their slot. Only visible cards in the
    selection move; the visible slice of each group is reordered independently.
    """
    selected = set(selected_slugs)
    group_order: list[str] = []
    group_cards: dict[str, list[Card]] = {}
    for c in model:
        if c.group not in group_cards:
            group_order.append(c.group)
            group_cards[c.group] = []
        group_cards[c.group].append(c)

    def reorder_slice(slice_cards: list[Card]) -> list[str]:
        slice_slugs = [c.slug for c in slice_cards]
        selected_in_slice = [s for s in slice_slugs if s in selected]
        if not selected_in_slice:
            return slice_slugs
        if action == "top":
            return selected_in_slice + [s for s in slice_slugs if s not in selected]
        if action == "bottom":
            return [s for s in slice_slugs if s not in selected] + selected_in_slice
        if action == "up":
            out = list(slice_slugs)
            for i in range(len(out)):
                if out[i] not in selected:
                    continue
                j = i - 1
                while j >= 0 and out[j] in selected:
                    j -= 1
                if j < 0:
                    continue
                out[j], out[i] = out[i], out[j]
            return out
        if action == "down":
            out = list(slice_slugs)
            for i in range(len(out) - 1, -1, -1):
                if out[i] not in selected:
                    continue
                j = i + 1
                while j < len(out) and out[j] in selected:
                    j += 1
                if j >= len(out):
                    continue
                out[j], out[i] = out[i], out[j]
            return out
        return slice_slugs

    result: list[str] = []
    for group in group_order:
        cards = group_cards[group]
        visible_slice = [c for c in cards if c.visible]
        new_slice_slugs = reorder_slice(visible_slice)
        visible_idx = 0
        for c in cards:
            if not c.visible:
                result.append(c.slug)
            else:
                result.append(new_slice_slugs[visible_idx])
                visible_idx += 1
    return result


def resolve_range(
    anchor_slug: str | None,
    target_slug: str,
    model: list[Card],
) -> list[str]:
    """Return visible slugs from anchor to target inclusive, in DOM order.

    If the target is hidden, returns []. If the anchor is hidden or missing,
    falls back to just the target.
    """
    visible_slugs = [c.slug for c in model if c.visible]
    try:
        idx_b = visible_slugs.index(target_slug)
    except ValueError:
        return []
    if anchor_slug is None:
        return [target_slug]
    try:
        idx_a = visible_slugs.index(anchor_slug)
    except ValueError:
        return [target_slug]
    lo, hi = sorted([idx_a, idx_b])
    return visible_slugs[lo : hi + 1]
