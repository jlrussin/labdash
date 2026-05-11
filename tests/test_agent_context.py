"""Tests for `labdash.agent_context` — the per-collection `AGENT_CONTEXT.md`
generator and migrator.

Covers the 8 cases from the 2026-05-12 plan:

  1. Fresh collection (no AGENT_CONTEXT.md, no agent_notes.md): file is seeded
     with AUTO blocks + a `## Conventions` placeholder. A second sync is a
     no-op (byte-identical regeneration is skipped).
  2. Migration from a collection-level `agent_notes.md`: AUTO preamble is
     spliced above the existing prose; the legacy file is deleted.
  3. Per-analysis migration: any `agent_notes.md` files under analysis
     directories are deleted on first sync.
  4. Idempotent re-sync: edits to the human body survive a re-sync; AUTO
     content is rewritten on structural change.
  5. Counts: groups, tags, output formats, statuses are reported correctly
     from a known fixture.
  6. Tag ordering is alphabetical (never by frequency).
  7. Malformed AUTO markers raise `AgentContextError`.
  8. Zero-count statuses / output formats are omitted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labdash import agent_context


AUTO_START = "<!-- AGENT_CONTEXT:AUTO:START -->"
AUTO_END = "<!-- AGENT_CONTEXT:AUTO:END -->"


# ── Fixtures ───────────────────────────────────────────────────────────


def _write_analysis(
    collection: Path,
    slug: str,
    *,
    group: str,
    tags: list[str] | None = None,
    status: str = "active",
    output_format: str = "png",
) -> None:
    """Create a minimal analysis directory: meta.yaml + analysis.py."""
    d = collection / slug
    d.mkdir()
    meta = {
        "title": slug.replace("_", " ").title(),
        "group": group,
        "description": "test",
        "methodology": "test",
        "tags": tags or [],
        "status": status,
        "output_format": output_format,
        "dependencies": [],
    }
    (d / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
    (d / "analysis.py").write_text(
        "def run(output_dir):\n"
        "    return {}\n"
    )


def _write_registry(collection: Path, *, groups: list[tuple[str, list[str]]],
                    tags: list[str] | None = None,
                    agent_notes: str = "") -> None:
    """Write a new-format registry.yaml."""
    data: dict = {
        "groups": [{"name": name, "analyses": list(slugs)} for name, slugs in groups],
        "tags": sorted(tags or []),
    }
    if agent_notes:
        data["agent_notes"] = agent_notes
    (collection / "registry.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def small_collection(tmp_path: Path) -> Path:
    """Two-group, five-analysis collection covering the count-rendering paths."""
    c = tmp_path / "demo"
    c.mkdir()
    _write_analysis(c, "a", group="Pipeline", tags=["pipeline"], output_format="pipeline")
    _write_analysis(c, "b", group="Pipeline", tags=["pipeline"], output_format="pipeline")
    _write_analysis(c, "c", group="Performance", tags=["accuracy", "rt"])
    _write_analysis(c, "d", group="Performance", tags=["accuracy"])
    _write_analysis(c, "e", group="Performance", tags=["rt"], output_format="table")
    _write_registry(
        c,
        groups=[("Pipeline", ["a", "b"]), ("Performance", ["c", "d", "e"])],
        tags=["accuracy", "pipeline", "rt"],
        agent_notes="Groups ordered Pipeline → Performance.",
    )
    return c


# ── Helpers ────────────────────────────────────────────────────────────


def _auto_region(text: str) -> str:
    """Return the slice between the START / END markers (inclusive)."""
    s = text.index(AUTO_START)
    e = text.index(AUTO_END) + len(AUTO_END)
    return text[s:e]


def _body_after_auto(text: str) -> str:
    """Return everything after the AUTO_END marker (trimmed)."""
    return text.split(AUTO_END, 1)[1].lstrip("\n")


# ── 1. Fresh collection + idempotence on re-sync ───────────────────────


def test_fresh_seeds_file_with_auto_and_conventions(small_collection: Path):
    out = agent_context.sync(small_collection)
    assert out is not None
    assert out.name == "AGENT_CONTEXT.md"
    text = out.read_text()
    assert AUTO_START in text
    assert AUTO_END in text
    assert "## Conventions" in _body_after_auto(text)


def test_resync_with_no_changes_is_noop(small_collection: Path):
    first = agent_context.sync(small_collection)
    assert first is not None
    before = first.read_text()
    second = agent_context.sync(small_collection)
    assert second is None  # byte-identical, no write
    assert first.read_text() == before


# ── 2. Migration from collection-level agent_notes.md ──────────────────


def test_migrates_collection_level_agent_notes(small_collection: Path):
    legacy = small_collection / "agent_notes.md"
    legacy.write_text(
        "# Collection-level agent notes: demo\n"
        "\n"
        "## Data loading\n"
        "- Use load_demo_data() from _lib/data_loading.py\n"
        "\n"
        "## Plotting\n"
        "- Always set ylabel explicitly after seaborn calls.\n"
    )

    out = agent_context.sync(small_collection)
    assert out is not None
    text = out.read_text()

    # AUTO preamble present; legacy file deleted.
    assert AUTO_START in text
    assert AUTO_END in text
    assert not legacy.exists()

    # Body below AUTO preserves the original prose verbatim, minus the H1.
    body = _body_after_auto(text)
    assert "## Data loading" in body
    assert "load_demo_data()" in body
    assert "## Plotting" in body
    # Original H1 should NOT appear (the new H1 is the generated one).
    assert "Collection-level agent notes: demo" not in body


# ── 3. Per-analysis agent_notes.md deletion ────────────────────────────


def test_per_analysis_notes_deleted_on_first_sync(small_collection: Path):
    n1 = small_collection / "a" / "agent_notes.md"
    n2 = small_collection / "c" / "agent_notes.md"
    n1.write_text("- gotcha A\n")
    n2.write_text("- gotcha C\n")

    agent_context.sync(small_collection)

    assert not n1.exists()
    assert not n2.exists()


# ── 4. Idempotent re-sync: human edits survive ─────────────────────────


def test_human_edits_below_auto_survive_resync(small_collection: Path):
    agent_context.sync(small_collection)
    target = small_collection / "AGENT_CONTEXT.md"

    # Append human content below AUTO.
    text = target.read_text()
    text += "\n## Notes\n\n- a hand-written observation\n"
    target.write_text(text)

    # Add a new analysis to force the AUTO region to change.
    _write_analysis(small_collection, "f", group="Performance", tags=["rt"])
    _write_registry(
        small_collection,
        groups=[("Pipeline", ["a", "b"]), ("Performance", ["c", "d", "e", "f"])],
        tags=["accuracy", "pipeline", "rt"],
        agent_notes="Groups ordered Pipeline → Performance.",
    )
    out = agent_context.sync(small_collection)
    assert out is not None

    new_text = out.read_text()
    # Human content preserved.
    assert "## Notes" in new_text
    assert "a hand-written observation" in new_text
    # AUTO content reflects the new analysis.
    assert "Performance (4)" in _auto_region(new_text)


# ── 5. Counts correctness ──────────────────────────────────────────────


def test_counts_match_fixture(small_collection: Path):
    out = agent_context.sync(small_collection)
    region = _auto_region(out.read_text())

    # Total
    assert "demo" in region
    assert "(5 analyses)" in region
    # Groups
    assert "- Pipeline (2)" in region
    assert "- Performance (3)" in region
    # Output formats: 3 png + 1 table + 2 pipeline ? — actually 2 pipeline (a, b),
    # 2 png (c, d), 1 table (e). All three should appear.
    assert "pipeline (2)" in region
    assert "png (2)" in region
    assert "table (1)" in region
    # Status (everything active)
    assert "active (5)" in region
    # Tags
    assert "accuracy (2)" in region
    assert "pipeline (2)" in region
    assert "rt (2)" in region


# ── 6. Tag ordering is alphabetical ────────────────────────────────────


def test_tags_alphabetical_regardless_of_frequency(tmp_path: Path):
    c = tmp_path / "demo"
    c.mkdir()
    # zebra is the most frequent — should still come last alphabetically.
    _write_analysis(c, "x", group="G", tags=["zebra", "alpha"])
    _write_analysis(c, "y", group="G", tags=["zebra"])
    _write_analysis(c, "z", group="G", tags=["zebra", "mid"])
    _write_registry(c, groups=[("G", ["x", "y", "z"])], tags=["alpha", "mid", "zebra"])
    out = agent_context.sync(c)
    region = _auto_region(out.read_text())

    # All three should appear in the tag list with their counts, alphabetical.
    i_alpha = region.index("alpha (")
    i_mid = region.index("mid (")
    i_zebra = region.index("zebra (")
    assert i_alpha < i_mid < i_zebra


# ── 7. Malformed markers raise ─────────────────────────────────────────


def test_missing_end_marker_raises(small_collection: Path):
    target = small_collection / "AGENT_CONTEXT.md"
    target.write_text(
        "# demo — agent context\n\n"
        + AUTO_START + "\n"
        + "(no end marker)\n"
    )
    with pytest.raises(agent_context.AgentContextError):
        agent_context.sync(small_collection)


def test_duplicate_start_marker_raises(small_collection: Path):
    target = small_collection / "AGENT_CONTEXT.md"
    target.write_text(
        "# demo — agent context\n\n"
        + AUTO_START + "\n"
        + AUTO_START + "\n"
        + AUTO_END + "\n"
    )
    with pytest.raises(agent_context.AgentContextError):
        agent_context.sync(small_collection)


def test_reversed_markers_raises(small_collection: Path):
    target = small_collection / "AGENT_CONTEXT.md"
    target.write_text(
        "# demo — agent context\n\n"
        + AUTO_END + "\n"
        + AUTO_START + "\n"
    )
    with pytest.raises(agent_context.AgentContextError):
        agent_context.sync(small_collection)


# ── 8. Zero-count statuses/formats omitted ─────────────────────────────


def test_omits_zero_counts(tmp_path: Path):
    c = tmp_path / "demo"
    c.mkdir()
    # Every analysis is active + png. Draft / publication / table / pipeline / svg
    # all zero — should not appear in the rendered preamble.
    for slug in ("a", "b", "c"):
        _write_analysis(c, slug, group="G", tags=["t"])
    _write_registry(c, groups=[("G", ["a", "b", "c"])], tags=["t"])
    region = _auto_region(agent_context.sync(c).read_text())

    assert "active (3)" in region
    assert "png (3)" in region
    # Zero counts must not appear at all (would be e.g. "draft (0)").
    assert "draft" not in region
    assert "publication" not in region
    assert "table" not in region
    assert "pipeline" not in region
    assert "svg" not in region
