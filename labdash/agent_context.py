"""Per-collection `AGENT_CONTEXT.md` generator and migrator.

Each LabDash collection carries a single `AGENT_CONTEXT.md` at its root.
The file has two regions:

  * an auto-generated **preamble** between two sentinel markers
    (`<!-- AGENT_CONTEXT:AUTO:START -->` … `<!-- AGENT_CONTEXT:AUTO:END -->`)
    that summarizes structural state (organizing principle, group counts,
    tag counts, output-format and status counts); regenerated on every
    `labdash build` / `serve` / metadata edit and on live-mode meta or
    registry changes
  * everything outside the markers — free-form human prose: conventions,
    data-loading notes, plotting rules, etc.

Sync replaces only the bytes inside the markers; content outside is
preserved verbatim. Malformed markers raise `AgentContextError` rather
than silently rewriting — sync never guesses.

On the *first* sync of a collection, the file may not exist yet:

  * if a legacy `agent_notes.md` exists at the collection root, its
    content (minus any leading H1) is preserved as the human body and the
    legacy file is deleted; any per-analysis `agent_notes.md` files under
    the collection are deleted too (consolidation per the 2026-05-12
    plan)
  * otherwise the file is seeded with the auto preamble + a single
    `## Conventions` placeholder

Public API:
    AgentContextError              — typed error
    sync(analyses_dir)             -> Path | None
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .languages import resolve_language_for
from .runner import _is_new_format_registry, discover_analyses, load_registry


AUTO_START = "<!-- AGENT_CONTEXT:AUTO:START -->"
AUTO_END = "<!-- AGENT_CONTEXT:AUTO:END -->"
FILENAME = "AGENT_CONTEXT.md"
LEGACY_FILENAME = "agent_notes.md"


class AgentContextError(Exception):
    """`AGENT_CONTEXT.md` has malformed AUTO markers."""


def sync(analyses_dir: Path) -> Path | None:
    """Regenerate `AGENT_CONTEXT.md` for one collection.

    Returns the path written, or `None` if the file was already up-to-date
    (byte-identical regeneration is skipped to avoid mtime bumps and git
    churn).

    Raises `AgentContextError` on malformed AUTO markers.
    """
    target = analyses_dir / FILENAME
    state = _collect_state(analyses_dir)
    auto = _render_auto(state)

    if target.exists():
        new_text = _splice(target.read_text(), auto, target)
    else:
        legacy = analyses_dir / LEGACY_FILENAME
        if legacy.exists():
            new_text = _migrate(analyses_dir, auto, legacy)
            legacy.unlink()
        else:
            new_text = _seed(analyses_dir, auto)
        # First-sync cleanup: drop any per-analysis `agent_notes.md` files.
        # Whether the collection had a collection-level legacy file or not,
        # per-analysis files are removed as part of the consolidation.
        _delete_per_analysis_notes(analyses_dir)

    if target.exists() and target.read_text() == new_text:
        return None

    target.write_text(new_text)
    return target


# ── State collection ──────────────────────────────────────────────────


def _collect_state(analyses_dir: Path) -> dict:
    """Read registry + meta.yaml files; compute counts.

    Returns a dict with keys:
      collection_name: str
      principle: str                  # registry.agent_notes verbatim
      groups: list[tuple[str, int]]   # (group_name, slug_count) in registry order
      tags: list[tuple[str, int]]     # alphabetical
      output_formats: list[tuple[str, int]]  # alphabetical, omit zeros
      statuses: list[tuple[str, int]]        # alphabetical, omit zeros
      total: int
    """
    analyses = discover_analyses(analyses_dir)
    registry = load_registry(analyses_dir)

    principle = ""
    group_order: list[str] = []
    if registry is not None and _is_new_format_registry(registry):
        principle = (registry.get("agent_notes") or "").strip()
        group_order = [g["name"] for g in registry.get("groups", []) if "name" in g]

    by_slug = {a["slug"]: a for a in analyses}

    # Build group → slug-count map from registry (the source of truth).
    group_counts: dict[str, int] = {name: 0 for name in group_order}
    seen: set[str] = set()
    if registry is not None and _is_new_format_registry(registry):
        for g in registry.get("groups", []):
            name = g.get("name", "")
            slugs = g.get("analyses", []) or []
            valid = [s for s in slugs if s in by_slug]
            group_counts[name] = len(valid)
            seen.update(valid)

    # Any analyses on disk but not in any registry group: drop into a
    # synthetic "(unassigned)" bucket so the count is still visible.
    unassigned = [a for a in analyses if a["slug"] not in seen]
    if unassigned:
        group_counts["(unassigned)"] = len(unassigned)
        group_order.append("(unassigned)")

    tag_counter: Counter[str] = Counter()
    fmt_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    for a in analyses:
        meta = a["meta"] or {}
        for t in meta.get("tags") or []:
            tag_counter[str(t)] += 1
        fmt_counter[str(meta.get("output_format") or "png")] += 1
        status_counter[str(meta.get("status") or "draft")] += 1

    language = resolve_language_for(analyses_dir)

    return {
        "collection_name": analyses_dir.name,
        "language": language.name,
        "principle": principle,
        "groups": [(name, group_counts[name]) for name in group_order],
        "tags": sorted(tag_counter.items()),
        "output_formats": sorted(fmt_counter.items()),
        "statuses": sorted(status_counter.items()),
        "total": len(analyses),
    }


# ── Render ────────────────────────────────────────────────────────────


def _render_auto(state: dict) -> str:
    """Render the auto preamble (markers included)."""
    lines: list[str] = []
    lines.append(AUTO_START)
    lines.append("")
    lines.append(
        "> Auto-generated by labdash. Regenerated on every `labdash build` /"
    )
    lines.append(
        "> `labdash serve` / metadata edit and on live-mode meta or registry"
    )
    lines.append(
        "> changes. **Do not edit between these markers — edits will be"
    )
    lines.append(
        "> overwritten on the next sync.** Free-form notes go below the END marker."
    )
    lines.append("")
    lines.append(f"**Collection:** `{state['collection_name']}` "
                 f"({state['total']} analyses)")
    lines.append(f"**Language:** {state['language']}")
    lines.append("")

    lines.append("## Organizing principle")
    lines.append("")
    if state["principle"]:
        lines.append(state["principle"])
    else:
        lines.append(
            "_No `agent_notes` set in `registry.yaml`. Edit there to populate._"
        )
    lines.append("")

    lines.append("## Groups")
    lines.append("")
    if state["groups"]:
        for name, count in state["groups"]:
            lines.append(f"- {name} ({count})")
    else:
        lines.append("_No groups yet._")
    lines.append("")

    lines.append("## Tags (alphabetical)")
    lines.append("")
    if state["tags"]:
        lines.append(", ".join(f"{t} ({n})" for t, n in state["tags"]))
    else:
        lines.append("_No tags in use._")
    lines.append("")

    lines.append("## Output formats")
    lines.append("")
    lines.append(", ".join(f"{f} ({n})" for f, n in state["output_formats"])
                 or "_None._")
    lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(", ".join(f"{s} ({n})" for s, n in state["statuses"])
                 or "_None._")
    lines.append("")

    lines.append(AUTO_END)

    return "\n".join(lines)


# ── Splice / seed / migrate ───────────────────────────────────────────


_HEADING_H1 = re.compile(r"^# .*$", re.MULTILINE)


def _splice(existing: str, auto: str, target: Path) -> str:
    """Replace the AUTO region in `existing` with `auto`.

    Raises `AgentContextError` if markers are missing, duplicated, or
    out of order.
    """
    starts = [m.start() for m in re.finditer(re.escape(AUTO_START), existing)]
    ends = [m.start() for m in re.finditer(re.escape(AUTO_END), existing)]

    if len(starts) == 0 and len(ends) == 0:
        raise AgentContextError(
            f"{target}: missing AUTO markers; expected one each of "
            f"{AUTO_START!r} and {AUTO_END!r}. Restore them or delete the file "
            "to let sync rebuild it."
        )
    if len(starts) != 1 or len(ends) != 1:
        raise AgentContextError(
            f"{target}: expected exactly one {AUTO_START!r} and one {AUTO_END!r}, "
            f"found {len(starts)} start(s) and {len(ends)} end(s)."
        )
    start_idx = starts[0]
    end_idx = ends[0] + len(AUTO_END)
    if start_idx > ends[0]:
        raise AgentContextError(
            f"{target}: {AUTO_END!r} appears before {AUTO_START!r}."
        )

    return existing[:start_idx] + auto + existing[end_idx:]


def _seed(analyses_dir: Path, auto: str) -> str:
    """Build a fresh `AGENT_CONTEXT.md` body."""
    name = analyses_dir.name
    parts = [
        f"# {name} — agent context",
        "",
        auto,
        "",
        "## Conventions",
        "",
        "",
    ]
    return "\n".join(parts)


def _migrate(analyses_dir: Path, auto: str, legacy: Path) -> str:
    """Build `AGENT_CONTEXT.md` body by migrating `agent_notes.md`.

    The legacy file's leading H1 (if any) is dropped; everything else is
    preserved verbatim and placed below the auto preamble. Trailing
    whitespace is normalized.
    """
    raw = legacy.read_text()
    lines = raw.splitlines()

    # Strip leading blank lines.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Drop a single leading H1 line + any blank line(s) that follow.
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

    body = "\n".join(lines[i:]).rstrip()

    name = analyses_dir.name
    parts = [
        f"# {name} — agent context",
        "",
        auto,
        "",
    ]
    if body:
        parts.append(body)
        parts.append("")
    else:
        parts.extend(["## Conventions", "", ""])
    return "\n".join(parts)


def _delete_per_analysis_notes(analyses_dir: Path) -> list[Path]:
    """Delete any per-analysis `agent_notes.md` under each slug directory.

    Returns the list of paths deleted (mainly for tests / logging). The
    collection-level `agent_notes.md` is the caller's responsibility.
    """
    deleted: list[Path] = []
    for child in sorted(analyses_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        candidate = child / LEGACY_FILENAME
        if candidate.exists():
            candidate.unlink()
            deleted.append(candidate)
    return deleted
