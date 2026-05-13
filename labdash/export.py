"""Export publication-ready figures and self-contained code."""

import json
import shutil
from pathlib import Path

import yaml

from .runner import discover_analyses, run_analysis, _resolve_run_order


def export_figures(analyses_dir: Path, output_dir: Path, dest: Path, *,
                   fmt: str = "svg", dpi: int = 300, status: str = "publication"):
    """Re-run publication analyses at high quality and copy to flat directory."""
    analyses = discover_analyses(analyses_dir)
    pub = [a for a in analyses if a["meta"].get("status") == status]

    if not pub:
        print(f"No analyses with status '{status}' found.")
        return

    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}

    for a in pub:
        slug = a["slug"]
        meta = a["meta"]
        # Pipeline nodes have no figure by default — skip unless a figure_id
        # is explicitly set (signals the scientist wants the optional image
        # that the pipeline node produced).
        if meta.get("output_format") == "pipeline" and not meta.get("figure_id"):
            print(f"  Skipping {slug} (pipeline node, no figure_id)")
            continue
        figure_id = meta.get("figure_id", slug)

        # Temporarily patch output format in the environment
        # The analysis should respect output_format from meta, but we override
        # by re-running to a temp output dir
        temp_out = output_dir / f"_export_{slug}"
        temp_out.mkdir(parents=True, exist_ok=True)

        print(f"  Exporting {slug} as {fmt}...", end=" ", flush=True)
        result = run_analysis(a, temp_out)

        if not result["success"]:
            print(f"FAIL")
            if result["error"]:
                for line in result["error"].strip().split("\n")[-3:]:
                    print(f"    {line}")
            continue

        # Find the output file and copy with figure_id name
        for ext in [f".{fmt}", ".png", ".svg", ".pdf"]:
            src = temp_out / slug / f"output{ext}"
            if not src.exists():
                # run_analysis writes to temp_out / slug; keep legacy location
                # as a fallback in case older analyses wrote to output_dir directly.
                src = temp_out / f"output{ext}"
            if src.exists():
                dest_name = f"{figure_id}{ext}"
                shutil.copy2(src, dest / dest_name)
                manifest[figure_id] = {"slug": slug, "file": dest_name, "caption": meta.get("caption", "")}
                print(f"OK → {dest_name}")
                break
        else:
            print("WARN: no output file found")

        # Clean up temp
        shutil.rmtree(temp_out, ignore_errors=True)

    # Write manifest
    manifest_path = dest / "figure_manifest.yaml"
    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False)
    print(f"\nExported {len(manifest)} figures to {dest}")
    print(f"Manifest: {manifest_path}")


def export_code(analyses_dir: Path, dest: Path, *, status: str = "publication"):
    """Gather analysis scripts + _lib into a self-contained directory.

    Includes pipeline-node analyses too, since they're real code with
    downstream dependents. run_all.py emits slugs in dependency order
    so transforms precede the leaves that consume them.
    """
    analyses = discover_analyses(analyses_dir)
    pub = [a for a in analyses if a["meta"].get("status") == status]

    if not pub:
        print(f"No analyses with status '{status}' found.")
        return

    dest.mkdir(parents=True, exist_ok=True)

    # Copy _lib (including _lib/plots/ if present)
    lib_src = analyses_dir / "_lib"
    if not lib_src.exists():
        # Maybe _lib/ is one level up (collection-layout projects)
        lib_src = analyses_dir.parent / "_lib"
    lib_dest = dest / "_lib"
    if lib_src.exists():
        if lib_dest.exists():
            shutil.rmtree(lib_dest)
        shutil.copytree(lib_src, lib_dest)
        print(f"  Copied _lib/")

    # Copy each analysis — preserve the wrapper filename (`analysis.py`
    # for Python, `analysis.R` for R).
    for a in pub:
        slug = a["slug"]
        slug_dest = dest / slug
        slug_dest.mkdir(parents=True, exist_ok=True)
        wrapper_name = a["analysis_path"].name
        shutil.copy2(a["analysis_path"], slug_dest / wrapper_name)
        meta_src = a["dir"] / "meta.yaml"
        if meta_src.exists():
            shutil.copy2(meta_src, slug_dest / "meta.yaml")
        print(f"  Copied {slug}/")

    # Emit run_all.py with slugs in dependency order (topological sort on the
    # published subset — deps outside the published set are silently dropped).
    ordered = _resolve_run_order(pub)
    run_all_code = '''"""Run all analyses to reproduce figures (in dependency order)."""
import sys
from pathlib import Path

# Add this directory to path for _lib imports
sys.path.insert(0, str(Path(__file__).parent))

ANALYSES = [
'''
    for a in ordered:
        run_all_code += f'    "{a["slug"]}",\n'
    run_all_code += ''']

if __name__ == "__main__":
    for slug in ANALYSES:
        print(f"Running {slug}...", end=" ", flush=True)
        try:
            import importlib
            mod = importlib.import_module(f"{slug}.analysis")
            output_dir = Path(__file__).parent / slug
            mod.run(output_dir)
            print("OK")
        except Exception as e:
            print(f"FAIL: {e}")
'''
    (dest / "run_all.py").write_text(run_all_code)

    # Generate requirements.txt from _lib imports (basic)
    reqs = "# Core dependencies for reproducing analyses\nnumpy\npandas\nmatplotlib\nseaborn\nscipy\n"
    (dest / "requirements.txt").write_text(reqs)

    print(f"\nExported {len(pub)} analyses to {dest}")


def export_pdf(analyses_dir: Path, output_dir: Path, dest: Path, *,
               status: str | None = None,
               groups: list[str] | None = None,
               tags: list[str] | None = None,
               include: list[str] | None = None,
               prefer_svg: bool = True,
               include_cover: bool = True,
               title: str | None = None) -> Path:
    """Render filtered analyses into a single PDF file at `dest`.

    Filters mirror the viewer: `status` matches `meta.status` exactly;
    `groups` and `tags` are inclusive sets. Slugs are emitted in registry
    order. `include` is a list of section names (figure, description,
    methodology, stats, code, shared_code, notes, tags, status) — anything
    not listed is omitted.
    """
    from .pdf_render import (
        render_pdf,
        resolve_slugs_from_filters,
        SectionToggles,
        PdfOptions,
    )

    slugs = resolve_slugs_from_filters(
        analyses_dir, status=status, groups=groups, tags=tags
    )
    if not slugs:
        print("No analyses matched the given filters.")
        return dest

    # Build SectionToggles from the include list. Anything explicitly named
    # is on; anything not named is off — EXCEPT we default the `status`
    # pill on, since it's purely metadata.
    if include is None:
        sections = SectionToggles()
    else:
        valid = set(SectionToggles.__dataclass_fields__.keys())
        unknown = [s for s in include if s not in valid]
        if unknown:
            print(f"  Warning: unknown section(s) ignored: {', '.join(unknown)}")
        chosen = {name: False for name in valid}
        for name in include:
            if name in chosen:
                chosen[name] = True
        chosen["status"] = True  # always show a status pill if non-active
        sections = SectionToggles(**chosen)

    print(f"  Rendering {len(slugs)} card(s) to PDF...")
    pdf_bytes = render_pdf(
        analyses_dir, output_dir, slugs,
        sections=sections,
        pdf_options=PdfOptions(
            prefer_svg=prefer_svg,
            title=title,
            include_cover=include_cover,
        ),
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)
    print(f"  Wrote {dest}")
    return dest
