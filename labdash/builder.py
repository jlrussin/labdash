"""Generates static HTML dashboard from analysis outputs."""

import os
import base64
import json
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader
from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import HtmlFormatter

from .runner import (
    discover_analyses,
    ordered_analyses,
    load_registry,
    _is_new_format_registry,
    is_stale,
    transitive_deps,
    _resolve_run_order,
)
from . import agent_context
from .lib_graph import (
    parse_lib_imports,
    build_lib_graph,
    transitive_lib_closure,
)


def _read_file(path: Path) -> str | None:
    """Read file contents or return None if missing."""
    if path.exists():
        return path.read_text()
    return None


def _image_to_data_uri(path: Path) -> str | None:
    """Convert image file to base64 data URI for embedding in HTML."""
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    mime_types = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}
    mime = mime_types.get(suffix, "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


PYGMENTS_STYLE = "monokai"


def _highlight_python(code: str) -> str:
    """Syntax-highlight Python code to HTML."""
    return highlight(code, PythonLexer(), HtmlFormatter(nowrap=True, style=PYGMENTS_STYLE))


def _parse_wrapper_imports(analysis_path: Path, lib_dir: Path) -> list[str]:
    """Return relative paths under `_lib/` that this wrapper directly imports.

    Thin wrapper around `lib_graph.parse_lib_imports`; returns a deterministic
    sorted list. Raises `LibImportError` on star imports, dynamic imports, or
    parse errors (strict-mandate enforcement).
    """
    return sorted(parse_lib_imports(analysis_path, lib_dir=lib_dir))


def _resolve_lib_dir(analyses_dir: Path) -> Path | None:
    """Find the _lib/ dir: first under analyses_dir, then its parent."""
    for d in [analyses_dir, analyses_dir.parent]:
        lib_dir = d / "_lib"
        if lib_dir.is_dir():
            return lib_dir
    return None


def build_card_data(
    analysis: dict,
    output_dir: Path,
    *,
    by_slug: dict[str, dict] | None = None,
    stale_memo: dict[str, bool] | None = None,
    lib_dir: Path | None = None,
    used_by_count: dict[str, int] | None = None,
) -> dict:
    """Build template context for a single analysis card.

    `by_slug` and `stale_memo` are threaded in so transitive staleness is
    computed once per build (memoized across all cards). `lib_dir` is the
    resolved `_lib/` directory for reading shared-module source. `used_by_count`
    maps "plots/X.py" → number of wrappers in this collection that import it,
    used to annotate shared tabs.
    """
    slug = analysis["slug"]
    meta = analysis["meta"]
    slug_output = output_dir / slug

    # Read wrapper source
    code = analysis["analysis_path"].read_text()
    code_highlighted = _highlight_python(code)

    # Shared modules imported by this wrapper (read source, highlight, annotate).
    # Tabs show DIRECT imports only — staleness is computed against the
    # transitive closure (stored separately on the analysis dict).
    shared_modules: list[dict] = []
    shared_rel = analysis.get("direct_shared_paths") or analysis.get("shared_paths", [])
    if lib_dir is not None:
        for rel in shared_rel:
            src = lib_dir / rel
            if not src.is_file():
                continue
            display_name = rel  # e.g. "plots/sde_accuracy.py"
            shared_modules.append({
                "name": rel,
                "display_name": display_name,
                "code_highlighted": _highlight_python(src.read_text()),
                "used_by_count": (used_by_count or {}).get(rel, 1),
            })

    # Read notes
    notes_path = analysis["dir"] / "notes.md"
    notes_raw = _read_file(notes_path)
    notes_html = markdown.markdown(notes_raw) if notes_raw else None

    # Read stats
    stats_path = slug_output / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else None

    # Determine output type and read it
    output_image = None
    output_table_html = None
    output_format = meta.get("output_format", "png")
    is_pipeline = (output_format == "pipeline")

    if output_format == "table":
        table_path = slug_output / "output.html"
        output_table_html = _read_file(table_path)
    else:
        # For `pipeline`, an image is optional — check common extensions.
        search_exts = [".png", ".svg", ".jpg"] if is_pipeline else [f".{output_format}", ".png", ".svg", ".jpg"]
        for ext in search_exts:
            img_path = slug_output / f"output{ext}"
            if img_path.exists():
                output_image = _image_to_data_uri(img_path)
                break

    if by_slug is None:
        by_slug = {}
    stale = is_stale(slug, by_slug, output_dir, stale_memo)

    # Lineage: transitive deps + self, topo-sorted.
    lineage = _build_lineage(slug, by_slug)

    return {
        "slug": slug,
        "title": meta.get("title", slug),
        "group": meta.get("group", "Ungrouped"),
        "order": meta.get("order", 999),  # legacy; ordering now comes from registry
        "description": meta.get("description", ""),
        "methodology": meta.get("methodology", ""),
        "tags": meta.get("tags", []),
        "status": meta.get("status", "draft"),
        "figure_id": meta.get("figure_id", ""),
        "caption": meta.get("caption", ""),
        "code": code,
        "code_highlighted": code_highlighted,
        "wrapper_code": code,
        "wrapper_code_highlighted": code_highlighted,
        "shared_modules": shared_modules,
        "lineage": lineage,
        "notes_raw": notes_raw or "",
        "notes_html": notes_html,
        "stats": stats,
        "output_image": output_image,
        "output_table_html": output_table_html,
        "stale": stale,
        "is_pipeline": is_pipeline,
        "output_format": output_format,
    }


def _build_lineage(slug: str, by_slug: dict[str, dict]) -> list[dict]:
    """Return transitive-dep chain (topo order) ending with the current slug.

    Each entry: {slug, title, is_pipeline, is_current}.
    """
    upstream = transitive_deps(slug, by_slug)
    if not upstream:
        # Still emit the current node so the renderer can choose whether to hide.
        a = by_slug.get(slug)
        if a is None:
            return []
        return [_lineage_segment(a, is_current=True)]

    subset = [by_slug[s] for s in upstream if s in by_slug]
    # Topo-sort the subset by resolving against the full by_slug DAG.
    ordered = _resolve_run_order(subset + [by_slug[slug]])
    return [_lineage_segment(a, is_current=(a["slug"] == slug)) for a in ordered]


def _lineage_segment(analysis: dict, *, is_current: bool) -> dict:
    meta = analysis.get("meta", {})
    return {
        "slug": analysis["slug"],
        "title": meta.get("title", analysis["slug"]),
        "is_pipeline": meta.get("output_format") == "pipeline",
        "is_current": is_current,
    }


def _build_lib_data(analyses_dir: Path, used_by: dict[str, list[str]] | None = None) -> dict:
    """Discover _lib/ sources and partition plots/ into used/unused sections.

    Returns:
        {
          "lib_files":    [{"name": "data_loading.py", "code_highlighted": ...}, ...],
          "plots_used":   [{"name": "plots/sde_accuracy.py", "display": "sde_accuracy.py",
                            "code_highlighted": ..., "used_by": ["sde_accuracy", ...]}, ...],
          "plots_unused": [{"name": "plots/unrelated.py", "display": "unrelated.py",
                            "code_highlighted": ...}, ...],
        }

    `used_by` maps "plots/X.py" → [slug, ...] of wrappers in this collection
    that import it. Files not present in used_by land in plots_unused.
    Top-level _lib/*.py is never filtered.
    """
    used_by = used_by or {}
    lib_files: list[dict] = []
    plots_used: list[dict] = []
    plots_unused: list[dict] = []

    lib_dir = _resolve_lib_dir(analyses_dir)
    if lib_dir is None:
        return {"lib_files": [], "plots_used": [], "plots_unused": []}

    for f in sorted(lib_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        lib_files.append({
            "name": f.name,
            "code_highlighted": _highlight_python(f.read_text()),
        })

    plots_dir = lib_dir / "plots"
    if plots_dir.is_dir():
        for f in sorted(plots_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            rel = f"plots/{f.name}"
            importers = used_by.get(rel, [])
            entry = {
                "name": rel,
                "display": f.name,
                "code_highlighted": _highlight_python(f.read_text()),
            }
            if importers:
                entry["used_by"] = sorted(importers)
                plots_used.append(entry)
            else:
                plots_unused.append(entry)

    return {
        "lib_files": lib_files,
        "plots_used": plots_used,
        "plots_unused": plots_unused,
    }


def _load_project_config(analyses_dir: Path) -> dict:
    """Load the collection's config from analyses_dir/collection.yaml."""
    collection_cfg = analyses_dir / "collection.yaml"
    if collection_cfg.exists():
        with open(collection_cfg) as f:
            return yaml.safe_load(f) or {}
    return {}


def build_dashboard(analyses_dir: Path, output_dir: Path) -> Path:
    """Generate the static HTML dashboard. Returns path to index.html."""
    # Sync registry first (handles migration, adds new analyses, removes deleted)
    _sync_registry(analyses_dir)
    agent_context.sync(analyses_dir)

    analyses = ordered_analyses(analyses_dir)
    by_slug = {a["slug"]: a for a in analyses}
    lib_dir = _resolve_lib_dir(analyses_dir)

    # Build the _lib import graph once; compute each wrapper's transitive
    # closure for staleness, but track DIRECT imports separately for UI
    # surfaces (per-card "shared code" tabs and the "used by N wrappers" badge
    # in the sidebar).
    lib_graph = build_lib_graph(lib_dir) if lib_dir is not None else {}

    used_by: dict[str, list[str]] = {}
    for a in analyses:
        if lib_dir is None:
            a["direct_shared_paths"] = []
            a["shared_paths"] = []
            continue
        direct = parse_lib_imports(a["analysis_path"], lib_dir=lib_dir)
        transitive = transitive_lib_closure(direct, lib_graph)
        a["direct_shared_paths"] = sorted(direct)
        a["shared_paths"] = sorted(transitive)  # consumed by is_stale
        for rel in direct:
            used_by.setdefault(rel, []).append(a["slug"])

    used_by_count = {k: len(v) for k, v in used_by.items()}

    stale_memo: dict[str, bool] = {}

    # Build card data for each analysis (memoized staleness, shared imports)
    cards = [
        build_card_data(
            a, output_dir,
            by_slug=by_slug,
            stale_memo=stale_memo,
            lib_dir=lib_dir,
            used_by_count=used_by_count,
        )
        for a in analyses
    ]

    # Organize by group as an ordered list of {name, cards} dicts
    groups_ordered = []
    groups_dict = {}
    for card in cards:
        g = card["group"]
        if g not in groups_dict:
            group_entry = {"name": g, "cards": []}
            groups_ordered.append(group_entry)
            groups_dict[g] = group_entry
        groups_dict[g]["cards"].append(card)

    # All unique tags
    all_tags = sorted({t for card in cards for t in card["tags"]})

    # All statuses present
    all_statuses = sorted({card["status"] for card in cards})

    # Shared _lib files for sidebar (partitioned used/unused)
    lib_data = _build_lib_data(analyses_dir, used_by=used_by)

    # Flat list used by client JS for openLibFile() lookups.
    all_lib_entries = (
        lib_data["lib_files"]
        + lib_data["plots_used"]
        + lib_data["plots_unused"]
    )

    # Project/collection config
    config = _load_project_config(analyses_dir)
    # Collection title: from config, or prettified directory name
    collection_title = config.get("title", config.get("project_name", ""))
    if not collection_title:
        collection_title = analyses_dir.name.replace("_", " ").replace("-", " ").title()

    # Load templates
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)
    template = env.get_template("index.html")

    # Pygments CSS (monokai for dark code blocks)
    pygments_css = HtmlFormatter(style=PYGMENTS_STYLE).get_style_defs(".highlight")

    html = template.render(
        cards=cards,
        groups=groups_ordered,
        all_tags=all_tags,
        all_statuses=all_statuses,
        pygments_css=pygments_css,
        total_count=len(cards),
        lib_data=lib_data,
        all_lib_entries=all_lib_entries,
        collection_title=collection_title,
    )

    index_path = output_dir / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(html)

    return index_path


def _sync_registry(analyses_dir: Path, analyses: list[dict] | None = None):
    """Sync registry.yaml with analyses on disk. Never reorders existing entries.

    - If registry is missing or old format: auto-migrate from meta.yaml order
    - Adds new analyses (on disk but not in registry) to end of their group
    - Removes deleted analyses (in registry but not on disk)
    - Removes empty groups
    - Re-collects tags from all meta.yaml files
    - Preserves agent_notes
    """
    if analyses is None:
        analyses = discover_analyses(analyses_dir)

    disk_slugs = {a["slug"] for a in analyses}
    by_slug = {a["slug"]: a for a in analyses}

    # Collect tags from all meta.yaml files
    all_tags = set()
    for a in analyses:
        for t in a["meta"].get("tags", []):
            all_tags.add(t)

    registry = load_registry(analyses_dir)

    if registry is not None and _is_new_format_registry(registry):
        # New format exists — sync it
        agent_notes = registry.get("agent_notes", "")

        # Build set of slugs currently in registry
        registry_slugs = set()
        for group in registry.get("groups", []):
            for slug in group.get("analyses", []):
                registry_slugs.add(slug)

        # Remove deleted slugs from groups
        new_groups = []
        for group in registry.get("groups", []):
            filtered = [s for s in group.get("analyses", []) if s in disk_slugs]
            if filtered:
                new_groups.append({"name": group["name"], "analyses": filtered})

        # Warn about group mismatches (registry wins for existing analyses)
        registry_group_for = {}
        for group in new_groups:
            for slug in group["analyses"]:
                registry_group_for[slug] = group["name"]
        for slug, reg_group in registry_group_for.items():
            if slug in by_slug:
                meta_group = by_slug[slug]["meta"].get("group", "Ungrouped")
                if meta_group != reg_group:
                    print(f"  Note: {slug} meta.yaml group \"{meta_group}\" "
                          f"differs from registry group \"{reg_group}\" (registry wins)")

        # Add new slugs (on disk but not in registry)
        new_slugs = disk_slugs - registry_slugs
        if new_slugs:
            # Sort new slugs by (group, slug) for deterministic insertion
            new_sorted = sorted(new_slugs, key=lambda s: (by_slug[s]["meta"].get("group", "Ungrouped"), s))
            groups_by_name = {g["name"]: g for g in new_groups}
            for slug in new_sorted:
                group_name = by_slug[slug]["meta"].get("group", "Ungrouped")
                if group_name in groups_by_name:
                    groups_by_name[group_name]["analyses"].append(slug)
                else:
                    new_group = {"name": group_name, "analyses": [slug]}
                    new_groups.append(new_group)
                    groups_by_name[group_name] = new_group
                print(f"  Added {slug} to group \"{group_name}\"")

        updated = {
            "agent_notes": agent_notes,
            "groups": new_groups,
            "tags": sorted(all_tags),
        }
        if not agent_notes:
            del updated["agent_notes"]

    else:
        # No registry or old format — migrate from meta.yaml order
        # discover_analyses already sorted by (group, order, slug)
        print("  Migrating to new registry format...")
        groups_ordered = []
        groups_dict = {}
        for a in analyses:
            group_name = a["meta"].get("group", "Ungrouped")
            if group_name not in groups_dict:
                group_entry = {"name": group_name, "analyses": []}
                groups_ordered.append(group_entry)
                groups_dict[group_name] = group_entry
            groups_dict[group_name]["analyses"].append(a["slug"])

        # Preserve agent_notes from old registry if present
        agent_notes = ""
        if registry is not None:
            agent_notes = registry.get("agent_notes", "")

        updated = {
            "groups": groups_ordered,
            "tags": sorted(all_tags),
        }
        if agent_notes:
            updated["agent_notes"] = agent_notes

        # Remove 'order' field from all meta.yaml files
        for a in analyses:
            meta_path = a["dir"] / "meta.yaml"
            if "order" in a["meta"]:
                del a["meta"]["order"]
                with open(meta_path, "w") as f:
                    yaml.dump(a["meta"], f, default_flow_style=False, sort_keys=False)
        print(f"  Migrated {len(analyses)} analyses into {len(groups_ordered)} groups")

    # Write registry
    registry_path = analyses_dir / "registry.yaml"
    with open(registry_path, "w") as f:
        yaml.dump(updated, f, default_flow_style=False, sort_keys=False)
