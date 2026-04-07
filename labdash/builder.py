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

from .runner import discover_analyses


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
        "order": meta.get("order", 999),
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
    """Load labdash.yaml from the project root."""
    for d in [analyses_dir, analyses_dir.parent, analyses_dir.parent.parent]:
        cfg = d / "labdash.yaml"
        if cfg.exists():
            with open(cfg) as f:
                return yaml.safe_load(f) or {}
    return {}


def build_dashboard(analyses_dir: Path, output_dir: Path) -> Path:
    """Generate the static HTML dashboard. Returns path to index.html."""
    analyses = discover_analyses(analyses_dir)

    # Build card data for each analysis
    cards = [build_card_data(a, output_dir) for a in analyses]

    # Organize by group
    groups = {}
    for card in cards:
        g = card["group"]
        if g not in groups:
            groups[g] = []
        groups[g].append(card)

    # All unique tags
    all_tags = sorted({t for card in cards for t in card["tags"]})

    # All statuses present
    all_statuses = sorted({card["status"] for card in cards})

    # Shared _lib files for sidebar
    lib_files = _build_lib_data(analyses_dir)

    # Project config for data filter badge
    config = _load_project_config(analyses_dir)
    data_filter = config.get("data_filter", "all")
    project_name = config.get("project_name", "")

    # Load templates
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)
    template = env.get_template("index.html")

    # Pygments CSS (monokai for dark code blocks)
    pygments_css = HtmlFormatter(style=PYGMENTS_STYLE).get_style_defs(".highlight")

    html = template.render(
        cards=cards,
        groups=groups,
        all_tags=all_tags,
        all_statuses=all_statuses,
        pygments_css=pygments_css,
        total_count=len(cards),
        lib_files=lib_files,
        data_filter=data_filter,
        project_name=project_name,
    )

    index_path = output_dir / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(html)

    # Update registry.yaml with current groups and tags
    _update_registry(analyses_dir, analyses)

    return index_path


def _update_registry(analyses_dir: Path, analyses: list[dict] | None = None):
    """Regenerate registry.yaml with current groups and tags."""
    if analyses is None:
        analyses = discover_analyses(analyses_dir)

    groups = {}
    tags = set()
    for a in analyses:
        meta = a["meta"]
        g = meta.get("group", "Ungrouped")
        if g not in groups:
            groups[g] = []
        groups[g].append(a["slug"])
        for t in meta.get("tags", []):
            tags.add(t)

    registry = {
        "groups": {g: sorted(slugs) for g, slugs in sorted(groups.items())},
        "tags": sorted(tags),
    }

    registry_path = analyses_dir / "registry.yaml"
    with open(registry_path, "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)
