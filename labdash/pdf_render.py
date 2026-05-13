"""Render a collection of cards into a single PDF via WeasyPrint.

Two entry points share the same core (`render_pdf`):
  - the HTTP route `POST /api/export/pdf` (server.py)
  - the CLI subcommand `labdash export pdf` (cli.py / export.py)

WeasyPrint is a soft dependency. It is imported lazily so that the rest of
labdash works without it; the renderer raises a clear error if PDF export
is attempted but the package is missing.
"""

from __future__ import annotations

import base64
import html as _html
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, FileSystemLoader
from pygments.formatters import HtmlFormatter

from .builder import build_card_data, _resolve_lib_dir
from .languages import resolve_language_for
from .lib_graph import build_lib_graph, transitive_lib_closure
from .runner import (
    discover_analyses,
    load_registry,
    _is_new_format_registry,
    is_stale,
)


# Light, print-friendly Pygments style. We deliberately do NOT use the
# viewer's "one-dark" since it requires a dark code background; the PDF
# uses a near-white code surface for ink-on-paper legibility.
PDF_PYGMENTS_STYLE = "friendly"


# ────────────────────────────────────────────────────────────────────
# Options
# ────────────────────────────────────────────────────────────────────


@dataclass
class SectionToggles:
    """Which sections to include for every card."""
    figure: bool = True
    description: bool = True
    methodology: bool = True
    stats: bool = True
    notes: bool = False
    code: bool = False
    shared_code: bool = False
    tags: bool = False
    status: bool = True  # render a status pill when not 'active'

    @classmethod
    def from_dict(cls, d: dict | None) -> "SectionToggles":
        if not d:
            return cls()
        valid = {k: bool(v) for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class PdfOptions:
    """Layout / packaging options."""
    prefer_svg: bool = True
    title: str | None = None  # overrides collection title shown in the running footer
    include_cover: bool = True  # render a cover page + groups-only TOC at the front


# ────────────────────────────────────────────────────────────────────
# Stats → HTML
# ────────────────────────────────────────────────────────────────────


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        # Tight numeric formatting: scientific for very small/large, else
        # up to 4 significant digits.
        if v == 0:
            return "0"
        av = abs(v)
        if av < 1e-3 or av >= 1e5:
            return f"{v:.3g}"
        if av >= 100:
            return f"{v:.1f}"
        return f"{v:.4g}"
    if v is None:
        return "—"
    if isinstance(v, dict):
        # Compact one-line summary for nested dicts that land in a table cell.
        return _html.escape(", ".join(f"{k}={_fmt_value(val)}" for k, val in v.items()))
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            # List of dicts: each dict on its own line, fields separated by commas.
            lines = ["; ".join(f"{k}={_fmt_value(val)}" for k, val in d.items()) for d in v]
            return _html.escape(" / ".join(lines))
        return _html.escape(", ".join(_fmt_value(x) for x in v))
    return _html.escape(str(v))


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _stat_item_html(key: str, value: Any) -> str:
    """Render a single key/value pair as a self-contained flex-pack chip."""
    return (
        f'<span class="stat-item">'
        f'<span class="k">{_html.escape(str(key))}</span>'
        f'<span class="v">{_fmt_value(value)}</span>'
        f'</span>'
    )


def _stats_to_html(stats: Any) -> str:
    """Render a stats payload as HTML suitable for embedding in a card.

    Handles three common shapes:
      - flat dict of scalars → flow-packed key/value chips
      - dict with a `"table"` list of dicts → HTML table (+ any sibling scalars chipped above)
      - nested dicts / mixed → flatten with indented sub-grids
    """
    if stats is None:
        return ""
    if not isinstance(stats, dict):
        return f'<div class="pdf-stats-grid">{_stat_item_html("value", stats)}</div>'

    parts: list[str] = []
    table = stats.get("table")
    scalar_keys = [k for k, v in stats.items() if k != "table" and _is_scalar(v)]
    nested_keys = [k for k, v in stats.items() if k != "table" and not _is_scalar(v)]

    if scalar_keys:
        parts.append('<div class="pdf-stats-grid">')
        for k in scalar_keys:
            parts.append(_stat_item_html(k, stats[k]))
        parts.append("</div>")

    if isinstance(table, list) and table and isinstance(table[0], dict):
        cols = list(table[0].keys())
        parts.append('<table class="pdf-stats-table"><thead><tr>')
        for c in cols:
            parts.append(f"<th>{_html.escape(str(c))}</th>")
        parts.append("</tr></thead><tbody>")
        for row in table:
            parts.append("<tr>")
            for c in cols:
                parts.append(f"<td>{_fmt_value(row.get(c))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    elif isinstance(table, list) and table:
        # list of scalars
        parts.append('<div class="pdf-stats-grid">')
        for i, v in enumerate(table):
            parts.append(
                f'<span class="k">[{i}]</span>'
                f'<span class="v">{_fmt_value(v)}</span>'
            )
        parts.append("</div>")

    for k in nested_keys:
        v = stats[k]
        parts.append(f'<div class="pdf-section-label pdf-stats-subhead">{_html.escape(str(k))}</div>')
        if isinstance(v, dict):
            parts.append(_stats_to_html(v))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            parts.append(_stats_to_html({"table": v}))
        elif isinstance(v, list):
            parts.append(_stats_to_html({"table": v}))
        else:
            parts.append(f'<div class="pdf-stats-grid">{_stat_item_html(k, v)}</div>')

    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────
# Card assembly
# ────────────────────────────────────────────────────────────────────


def _ordered_slugs_from_registry(analyses_dir: Path, available_slugs: set[str]) -> list[str]:
    """Return slugs in registry order; any slugs in the set but not in the
    registry are appended (alphabetical) at the end.
    """
    registry = load_registry(analyses_dir)
    ordered: list[str] = []
    if registry is not None and _is_new_format_registry(registry):
        for group in registry.get("groups", []):
            for slug in group.get("analyses", []):
                if slug in available_slugs:
                    ordered.append(slug)
    seen = set(ordered)
    leftovers = sorted(s for s in available_slugs if s not in seen)
    return ordered + leftovers


def _image_to_data_uri(path: Path) -> str | None:
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".svg": "image/svg+xml",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _maybe_prefer_svg(card: dict, output_dir: Path) -> None:
    """If a higher-fidelity SVG sits alongside the PNG, swap output_image to it."""
    slug = card["slug"]
    svg = output_dir / slug / "output.svg"
    if svg.exists():
        uri = _image_to_data_uri(svg)
        if uri:
            card["output_image"] = uri


def _has_renderable_output(card: dict, sections: SectionToggles) -> bool:
    """Decide whether a card has anything worth rendering.

    A card is renderable if any of the toggled sections produce content.
    Pipeline cards with no figure/table are still renderable if at least
    one of their text fields (description, methodology, notes, stats) is
    enabled AND populated.
    """
    if sections.figure and (card.get("output_image") or card.get("output_table_html")):
        return True
    if sections.description and card.get("description"):
        return True
    if sections.methodology and card.get("methodology"):
        return True
    if sections.stats and card.get("stats"):
        return True
    if sections.notes and card.get("notes_html"):
        return True
    if sections.code and card.get("wrapper_code_highlighted"):
        return True
    return False


def _build_cards(
    analyses_dir: Path,
    output_dir: Path,
    slugs: list[str],
    sections: SectionToggles,
    prefer_svg: bool,
) -> tuple[list[dict], list[str]]:
    """Build card-data dicts for the requested slugs, in registry order.

    Returns (rendered_cards, skipped_slugs). A slug is `skipped` if it
    exists on disk but produces no renderable content under the section
    toggles.
    """
    language = resolve_language_for(analyses_dir)
    analyses = discover_analyses(analyses_dir, language=language)
    by_slug = {a["slug"]: a for a in analyses}

    requested = set(slugs)
    available = requested & set(by_slug.keys())
    ordered_slugs = _ordered_slugs_from_registry(analyses_dir, available)

    lib_dir = _resolve_lib_dir(analyses_dir)

    # Build the _lib import graph + per-wrapper transitive closure so the
    # `stale` flag is computed against the same closure the viewer uses.
    # Mirrors builder.build_dashboard()'s setup.
    if lib_dir is not None:
        lib_graph = build_lib_graph(lib_dir, language=language)
        for a in analyses:
            direct = language.parse_lib_imports(a["analysis_path"], lib_dir)
            transitive = transitive_lib_closure(direct, lib_graph)
            a["direct_shared_paths"] = sorted(direct)
            a["shared_paths"] = sorted(transitive)
    else:
        for a in analyses:
            a["direct_shared_paths"] = []
            a["shared_paths"] = []

    stale_memo: dict[str, bool] = {}
    cards: list[dict] = []
    skipped: list[str] = []

    for slug in ordered_slugs:
        a = by_slug[slug]
        card = build_card_data(
            a, output_dir,
            by_slug=by_slug,
            stale_memo=stale_memo,
            lib_dir=lib_dir,
        )
        if prefer_svg:
            _maybe_prefer_svg(card, output_dir)
        if _has_renderable_output(card, sections):
            cards.append(card)
        else:
            skipped.append(slug)

    # Also surface requested slugs that didn't exist on disk at all.
    missing = sorted(requested - set(by_slug.keys()))
    skipped = skipped + missing

    return cards, skipped


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────


def _load_collection_title(analyses_dir: Path) -> str:
    cfg = analyses_dir / "collection.yaml"
    if cfg.exists():
        import yaml
        with open(cfg) as f:
            data = yaml.safe_load(f) or {}
        title = data.get("title")
        if title:
            return title
    return analyses_dir.name.replace("_", " ").replace("-", " ").title()


def render_pdf(
    analyses_dir: Path,
    output_dir: Path,
    slugs: list[str],
    sections: SectionToggles | dict | None = None,
    pdf_options: PdfOptions | None = None,
) -> bytes:
    """Render the requested slugs into a single PDF; return raw bytes.

    Raises `RuntimeError` if WeasyPrint is not installed.
    """
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise RuntimeError(
            "PDF export requires WeasyPrint. Install with: pip install labdash[pdf]"
        ) from e

    if isinstance(sections, dict) or sections is None:
        sections = SectionToggles.from_dict(sections if isinstance(sections, dict) else None)
    if pdf_options is None:
        pdf_options = PdfOptions()

    cards, skipped = _build_cards(
        analyses_dir, output_dir, slugs, sections, pdf_options.prefer_svg
    )

    # Mark the first card of each group + collect groups for the TOC.
    groups: list[dict] = []
    seen: dict[str, dict] = {}
    for card in cards:
        g = card.get("group") or "Ungrouped"
        if g not in seen:
            entry = {"name": g, "count": 1, "first_slug": card["slug"]}
            seen[g] = entry
            groups.append(entry)
            card["is_group_first"] = True
        else:
            seen[g]["count"] += 1
            card["is_group_first"] = False

    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)
    env.globals["stats_html"] = _stats_to_html
    template = env.get_template("export_pdf.html")
    pdf_css = (template_dir / "pdf.css").read_text()
    pygments_css = HtmlFormatter(style=PDF_PYGMENTS_STYLE).get_style_defs(".pdf-code .highlight")

    title = pdf_options.title or _load_collection_title(analyses_dir)
    from datetime import date
    today = date.today().isoformat()

    # If the user wants a cover but only Ungrouped cards exist (and only one
    # group), the TOC adds little; still render it for consistency, but the
    # toggle remains user-controlled.
    include_cover = bool(pdf_options.include_cover and cards)

    html = template.render(
        cards=cards,
        sections=sections,
        skipped=skipped,
        collection_title=title,
        pdf_css=pdf_css,
        pygments_css=pygments_css,
        groups=groups,
        include_cover=include_cover,
        today=today,
    )

    return HTML(string=html, base_url=str(analyses_dir)).write_pdf()


def resolve_slugs_from_filters(
    analyses_dir: Path,
    *,
    status: str | None = None,
    groups: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> list[str]:
    """For the CLI: turn filter args into a slug list in registry order.

    `status` matches `meta.status` exactly. `groups` and `tags` are inclusive
    sets (match if `meta.group` is in `groups`, or any of `meta.tags` is in
    `tags`). Mirrors the viewer's filter semantics.
    """
    language = resolve_language_for(analyses_dir)
    analyses = discover_analyses(analyses_dir, language=language)
    groups_set = set(groups) if groups else None
    tags_set = set(tags) if tags else None

    matched: set[str] = set()
    for a in analyses:
        meta = a["meta"]
        if status and meta.get("status") != status:
            continue
        if groups_set and meta.get("group") not in groups_set:
            continue
        if tags_set and not (set(meta.get("tags", [])) & tags_set):
            continue
        matched.add(a["slug"])

    return _ordered_slugs_from_registry(analyses_dir, matched)
