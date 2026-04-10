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

from .runner import discover_analyses, ordered_analyses, load_registry, _is_new_format_registry


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


def _is_stale(analysis_path: Path, output_dir: Path, slug: str) -> bool:
    """Check if output is older than analysis.py source."""
    output_png = output_dir / slug / "output.png"
    output_html = output_dir / slug / "output.html"
    output = output_png if output_png.exists() else output_html
    if not output.exists():
        return True
    return analysis_path.stat().st_mtime > output.stat().st_mtime


def build_card_data(analysis: dict, output_dir: Path) -> dict:
    """Build template context for a single analysis card."""
    slug = analysis["slug"]
    meta = analysis["meta"]
    slug_output = output_dir / slug

    # Read source code
    code = analysis["analysis_path"].read_text()
    code_highlighted = _highlight_python(code)

    # Read notes
    notes_path = analysis["dir"] / "notes.md"
    notes_raw = _read_file(notes_path)
    notes_html = markdown.markdown(notes_raw) if notes_raw else None

    # Read agent notes
    agent_notes_path = analysis["dir"] / "agent_notes.md"
    agent_notes_raw = _read_file(agent_notes_path)
    agent_notes_html = markdown.markdown(agent_notes_raw) if agent_notes_raw else None

    # Read stats
    stats_path = slug_output / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else None

    # Determine output type and read it
    output_image = None
    output_table_html = None
    output_format = meta.get("output_format", "png")

    if output_format == "table":
        table_path = slug_output / "output.html"
        output_table_html = _read_file(table_path)
    else:
        for ext in [f".{output_format}", ".png", ".svg", ".jpg"]:
            img_path = slug_output / f"output{ext}"
            if img_path.exists():
                output_image = _image_to_data_uri(img_path)
                break

    stale = _is_stale(analysis["analysis_path"], output_dir, slug)

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
        "notes_raw": notes_raw or "",
        "notes_html": notes_html,
        "agent_notes_html": agent_notes_html,
        "stats": stats,
        "output_image": output_image,
        "output_table_html": output_table_html,
        "stale": stale,
    }


def _build_lib_data(analyses_dir: Path) -> list[dict]:
    """Discover and read _lib/ source files for display in sidebar."""
    lib_files = []
    # Walk up from analyses_dir to find _lib (collection layout)
    for d in [analyses_dir, analyses_dir.parent]:
        lib_dir = d / "_lib"
        if lib_dir.is_dir():
            for f in sorted(lib_dir.glob("*.py")):
                if f.name == "__init__.py":
                    continue
                code = f.read_text()
                lib_files.append({
                    "name": f.name,
                    "code_highlighted": _highlight_python(code),
                })
            break
    return lib_files


def _load_project_config(analyses_dir: Path) -> dict:
    """Load config: collection.yaml in analyses_dir, then labdash.yaml walking up."""
    # Prefer collection-level config
    collection_cfg = analyses_dir / "collection.yaml"
    if collection_cfg.exists():
        with open(collection_cfg) as f:
            config = yaml.safe_load(f) or {}
        return config

    # Fall back to labdash.yaml
    for d in [analyses_dir, analyses_dir.parent, analyses_dir.parent.parent]:
        cfg = d / "labdash.yaml"
        if cfg.exists():
            with open(cfg) as f:
                return yaml.safe_load(f) or {}
    return {}


def build_dashboard(analyses_dir: Path, output_dir: Path) -> Path:
    """Generate the static HTML dashboard. Returns path to index.html."""
    # Sync registry first (handles migration, adds new analyses, removes deleted)
    _sync_registry(analyses_dir)

    analyses = ordered_analyses(analyses_dir)

    # Build card data for each analysis
    cards = [build_card_data(a, output_dir) for a in analyses]

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

    # Shared _lib files for sidebar
    lib_files = _build_lib_data(analyses_dir)

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
        lib_files=lib_files,
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
