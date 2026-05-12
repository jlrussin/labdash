"""Tests for `labdash.builder._sync_registry` after Option B.

Under Option B, `meta.yaml.group` is authoritative for membership and
`registry.yaml` is the layout manifest (group order + within-group order).
The sync moves slugs to follow `meta.group`; on-disk edits to `meta.group`
for an existing analysis are no longer silently ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labdash.builder import _sync_registry


# ── Fixtures ───────────────────────────────────────────────────────────


def _write_analysis(
    collection: Path,
    slug: str,
    *,
    group: str | None,
    tags: list[str] | None = None,
) -> None:
    d = collection / slug
    d.mkdir()
    meta: dict = {
        "title": slug.replace("_", " ").title(),
        "description": "test",
        "methodology": "test",
        "tags": tags or [],
        "status": "active",
        "output_format": "png",
        "dependencies": [],
    }
    if group is not None:
        meta["group"] = group
    (d / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
    (d / "analysis.py").write_text("def run(output_dir):\n    return {}\n")


def _write_registry(
    collection: Path,
    *,
    groups: list[tuple[str, list[str]]],
    tags: list[str] | None = None,
    agent_notes: str = "",
) -> None:
    data: dict = {
        "groups": [{"name": name, "analyses": list(slugs)} for name, slugs in groups],
        "tags": sorted(tags or []),
    }
    if agent_notes:
        data["agent_notes"] = agent_notes
    (collection / "registry.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _load_registry(collection: Path) -> dict:
    return yaml.safe_load((collection / "registry.yaml").read_text())


def _groups(reg: dict) -> dict[str, list[str]]:
    return {g["name"]: list(g["analyses"]) for g in reg["groups"]}


def _group_order(reg: dict) -> list[str]:
    return [g["name"] for g in reg["groups"]]


# ── Phase A semantics ──────────────────────────────────────────────────


def test_meta_group_disk_edit_moves_slug_on_sync(tmp_path: Path):
    """If meta.group disagrees with the registry, sync moves the slug."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1")
    _write_analysis(c, "b", group="g2")  # meta says g2 but registry will put it in g1
    _write_registry(c, groups=[("g1", ["a", "b"]), ("g2", [])])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g == {"g1": ["a"], "g2": ["b"]}


def test_meta_group_new_group_creates_registry_entry(tmp_path: Path):
    """A meta.group referring to a group not in the registry creates it."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1")
    _write_analysis(c, "b", group="brand_new")
    _write_registry(c, groups=[("g1", ["a"])])

    _sync_registry(c)

    reg = _load_registry(c)
    assert _group_order(reg) == ["g1", "brand_new"]
    assert _groups(reg) == {"g1": ["a"], "brand_new": ["b"]}


def test_meta_group_unchanged_preserves_within_group_order(tmp_path: Path):
    """When meta.group matches the registry, the slug stays put — order preserved."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "alpha", group="g1")
    _write_analysis(c, "bravo", group="g1")
    _write_analysis(c, "charlie", group="g1")
    # Intentional non-alphabetical order to assert preservation.
    _write_registry(c, groups=[("g1", ["bravo", "alpha", "charlie"])])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g == {"g1": ["bravo", "alpha", "charlie"]}


def test_meta_group_empty_groups_pruned(tmp_path: Path):
    """A registry group whose slugs all left it (because meta.group changed)
    is removed entirely."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g2")  # was in g1 per registry, now declares g2
    _write_registry(c, groups=[("g1", ["a"]), ("g2", [])])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g == {"g2": ["a"]}
    assert "g1" not in g


def test_sync_no_op_when_already_aligned(tmp_path: Path):
    """Running sync twice on an aligned collection writes the same bytes."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1", tags=["x"])
    _write_analysis(c, "b", group="g2", tags=["y"])
    _write_registry(c, groups=[("g1", ["a"]), ("g2", ["b"])], tags=["x", "y"])

    _sync_registry(c)
    snapshot = (c / "registry.yaml").read_text()
    _sync_registry(c)
    assert (c / "registry.yaml").read_text() == snapshot


def test_missing_meta_group_defaults_to_ungrouped(tmp_path: Path):
    """A meta.yaml without a `group:` field places the slug in 'Ungrouped'."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group=None)
    _write_registry(c, groups=[])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g == {"Ungrouped": ["a"]}


def test_deleted_slug_removed_from_registry(tmp_path: Path):
    """A slug present in the registry but missing on disk is dropped."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1")
    _write_registry(c, groups=[("g1", ["a", "ghost"])])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g == {"g1": ["a"]}


def test_multiple_new_slugs_into_new_group_are_alphabetical(tmp_path: Path):
    """When several disk slugs all target a previously-absent group,
    they're appended in alphabetical order — deterministic."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "z_slug", group="new_g")
    _write_analysis(c, "a_slug", group="new_g")
    _write_analysis(c, "m_slug", group="new_g")
    _write_registry(c, groups=[])

    _sync_registry(c)

    g = _groups(_load_registry(c))
    assert g["new_g"] == ["a_slug", "m_slug", "z_slug"]


def test_tags_recollected_from_meta(tmp_path: Path):
    """Registry tags are derived (sorted, unique) from meta.tags."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1", tags=["beta", "alpha"])
    _write_analysis(c, "b", group="g1", tags=["alpha", "gamma"])
    _write_registry(c, groups=[("g1", ["a", "b"])], tags=["stale"])

    _sync_registry(c)

    reg = _load_registry(c)
    assert reg["tags"] == ["alpha", "beta", "gamma"]


def test_agent_notes_preserved(tmp_path: Path):
    """Existing `agent_notes` survives sync."""
    c = tmp_path / "col"
    c.mkdir()
    _write_analysis(c, "a", group="g1")
    _write_registry(c, groups=[("g1", ["a"])], agent_notes="Keep me.")

    _sync_registry(c)

    reg = _load_registry(c)
    assert reg.get("agent_notes") == "Keep me."
