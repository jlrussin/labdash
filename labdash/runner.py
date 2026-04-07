"""Discovers and executes analysis scripts."""

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

import yaml


def discover_analyses(analyses_dir: Path) -> list[dict]:
    """Find all analysis directories containing analysis.py + meta.yaml.

    Returns list of dicts with keys: slug, dir, meta, analysis_path.
    Sorted by (group, order, slug).
    """
    analyses = []
    for d in sorted(analyses_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        analysis_py = d / "analysis.py"
        meta_yaml = d / "meta.yaml"
        if not analysis_py.exists() or not meta_yaml.exists():
            continue
        with open(meta_yaml) as f:
            meta = yaml.safe_load(f)
        if meta.get("status") == "disabled":
            continue
        analyses.append({
            "slug": d.name,
            "dir": d,
            "meta": meta,
            "analysis_path": analysis_py,
        })
    analyses.sort(key=lambda a: (a["meta"].get("group", ""), a["meta"].get("order", 999), a["slug"]))
    return analyses


def _resolve_run_order(analyses: list[dict]) -> list[dict]:
    """Topological sort based on meta.yaml dependencies field."""
    by_slug = {a["slug"]: a for a in analyses}
    visited = set()
    order = []

    def visit(slug):
        if slug in visited:
            return
        visited.add(slug)
        a = by_slug.get(slug)
        if a is None:
            return
        for dep in a["meta"].get("dependencies", []):
            visit(dep)
        order.append(a)

    for a in analyses:
        visit(a["slug"])
    return order


def run_analysis(analysis: dict, output_dir: Path) -> dict:
    """Execute a single analysis script. Returns result dict."""
    slug = analysis["slug"]
    analysis_path = analysis["analysis_path"]
    analyses_root = analysis_path.parent.parent
    slug_output = output_dir / slug
    slug_output.mkdir(parents=True, exist_ok=True)

    # Add analyses root and its parent to sys.path so _lib imports work.
    # _lib/ may be in the collection dir (analyses_root) or one level up
    # (for collection-based layouts where _lib/ is shared across collections).
    for path in [analyses_root, analyses_root.parent]:
        path_str = str(path)
        if path_str not in sys.path and (path / "_lib").is_dir():
            sys.path.insert(0, path_str)

    result = {
        "slug": slug,
        "success": False,
        "stats": None,
        "error": None,
        "duration_s": 0,
    }

    t0 = time.time()
    try:
        spec = importlib.util.spec_from_file_location(f"analysis_{slug}", str(analysis_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        stats = mod.run(slug_output)
        result["success"] = True
        result["stats"] = stats or {}
        # Save stats.json
        if stats:
            with open(slug_output / "stats.json", "w") as f:
                json.dump(stats, f, indent=2, default=str)
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        result["duration_s"] = round(time.time() - t0, 2)

    return result


def run_all(analyses_dir: Path, output_dir: Path, slugs: list[str] | None = None) -> list[dict]:
    """Discover and run analyses. If slugs provided, only run those."""
    all_analyses = discover_analyses(analyses_dir)
    if slugs:
        slug_set = set(slugs)
        to_run = [a for a in all_analyses if a["slug"] in slug_set]
        missing = slug_set - {a["slug"] for a in to_run}
        if missing:
            print(f"Warning: analyses not found: {', '.join(sorted(missing))}")
    else:
        to_run = all_analyses

    ordered = _resolve_run_order(to_run)
    results = []
    for a in ordered:
        print(f"  Running {a['slug']}...", end=" ", flush=True)
        r = run_analysis(a, output_dir)
        status = "OK" if r["success"] else "FAIL"
        print(f"{status} ({r['duration_s']}s)")
        if r["error"]:
            # Print first 5 lines of traceback
            lines = r["error"].strip().split("\n")
            for line in lines[-5:]:
                print(f"    {line}")
        results.append(r)

    return results
