"""Discovers and executes analysis scripts."""

import importlib.util
import json
import math
import sys
import time
import traceback
from pathlib import Path

import yaml

from . import _context


def discover_analyses(analyses_dir: Path) -> list[dict]:
    """Find all analysis directories containing analysis.py + meta.yaml.

    Returns list of dicts with keys: slug, dir, meta, analysis_path.
    Sorted by (group, order, slug) as a fallback ordering.
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


def load_registry(analyses_dir: Path) -> dict | None:
    """Load and parse registry.yaml. Returns None if missing or invalid.

    New format has groups as a list of dicts:
      groups: [{name: "...", analyses: [...]}, ...]
    Old format has groups as a dict:
      groups: {"GroupName": ["slug1", ...]}
    """
    registry_path = analyses_dir / "registry.yaml"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        print(f"Warning: failed to parse registry.yaml: {e}")
        return None


def _is_new_format_registry(registry: dict) -> bool:
    """Check if registry uses the new list-of-dicts format for groups."""
    groups = registry.get("groups")
    if not isinstance(groups, list):
        return False
    if len(groups) == 0:
        return True
    return isinstance(groups[0], dict)


def ordered_analyses(analyses_dir: Path) -> list[dict]:
    """Return analyses in registry order (group order -> within-group order).

    Falls back to meta.yaml-based ordering if no new-format registry exists.
    Analyses on disk but not in registry are appended at the end.
    """
    all_analyses = discover_analyses(analyses_dir)
    registry = load_registry(analyses_dir)

    if registry is None or not _is_new_format_registry(registry):
        # No new-format registry — use meta.yaml ordering (already sorted by discover_analyses)
        return all_analyses

    by_slug = {a["slug"]: a for a in all_analyses}
    ordered = []
    seen = set()

    for group in registry.get("groups", []):
        for slug in group.get("analyses", []):
            if slug in by_slug and slug not in seen:
                ordered.append(by_slug[slug])
                seen.add(slug)

    # Append analyses not in registry (new ones, sorted by group/order/slug)
    for a in all_analyses:
        if a["slug"] not in seen:
            ordered.append(a)

    return ordered


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


# ── Staleness ──────────────────────────────────────────────────────────


def _oldest_output_mtime(output_dir: Path) -> float | None:
    """Mtime of the oldest file in output_dir, or None if none exist.

    Used as the "baseline" for a slug: if anything is newer than this,
    the slug is stale. Ignores subdirectories.
    """
    if not output_dir.exists():
        return None
    mtimes = [p.stat().st_mtime for p in output_dir.iterdir() if p.is_file()]
    if not mtimes:
        return None
    return min(mtimes)


def _newest_output_mtime(output_dir: Path) -> float:
    """Mtime of the newest file in output_dir, or -inf if none exist.

    Used as the "latest produced" mtime for an upstream dep: if this is
    newer than a downstream's baseline, the downstream is stale.
    """
    if not output_dir.exists():
        return -math.inf
    mtimes = [p.stat().st_mtime for p in output_dir.iterdir() if p.is_file()]
    if not mtimes:
        return -math.inf
    return max(mtimes)


def is_stale(
    slug: str,
    by_slug: dict[str, dict],
    output_root: Path,
    _memo: dict[str, bool] | None = None,
) -> bool:
    """Return True if `slug` needs to be rerun.

    Transitive rules:
    - self-stale if analysis.py is newer than the oldest of its output files
      (or if its output dir is empty);
    - dep-stale if any upstream dep's newest output is newer than this
      slug's oldest output;
    - recursively stale if any upstream dep is itself stale.

    Memoization prevents exponential re-traversal in deep DAGs.
    """
    if _memo is None:
        _memo = {}
    if slug in _memo:
        return _memo[slug]

    analysis = by_slug.get(slug)
    if analysis is None:
        # Missing analysis; treat as stale so the caller surfaces the problem.
        _memo[slug] = True
        return True

    out_dir = output_root / slug
    my_oldest = _oldest_output_mtime(out_dir)

    # No output yet → stale.
    if my_oldest is None:
        _memo[slug] = True
        return True

    # Self-stale: source newer than my oldest output.
    if analysis["analysis_path"].stat().st_mtime > my_oldest:
        _memo[slug] = True
        return True

    for dep in analysis["meta"].get("dependencies", []) or []:
        dep_dir = output_root / dep
        dep_newest = _newest_output_mtime(dep_dir)
        if dep_newest > my_oldest:
            _memo[slug] = True
            return True
        if is_stale(dep, by_slug, output_root, _memo):
            _memo[slug] = True
            return True

    _memo[slug] = False
    return False


def transitive_deps(slug: str, by_slug: dict[str, dict]) -> list[str]:
    """All transitive dependency slugs of `slug`, excluding `slug` itself.

    Order within the list is not meaningful; callers that care should
    feed the result back through a topological sort.
    """
    collected: set[str] = set()
    stack = [slug]
    while stack:
        current = stack.pop()
        a = by_slug.get(current)
        if a is None:
            continue
        for dep in a["meta"].get("dependencies", []) or []:
            if dep not in collected:
                collected.add(dep)
                stack.append(dep)
    collected.discard(slug)
    return list(collected)


def expand_with_stale_upstream(
    target_slugs: list[str],
    all_analyses: list[dict],
    output_root: Path,
) -> list[dict]:
    """Expand target slugs with all transitively-stale upstream, topo-sorted.

    For each target slug:
    - the target itself is always included (Run means run);
    - any transitive dep that `is_stale` is also included.

    Topological sort is over the full DAG (all_analyses), then filtered
    to the final set so that run order respects deps even if intermediate
    non-stale nodes are skipped.
    """
    by_slug = {a["slug"]: a for a in all_analyses}
    memo: dict[str, bool] = {}

    to_run: set[str] = set()
    for slug in target_slugs:
        if slug not in by_slug:
            continue
        to_run.add(slug)
        for dep in transitive_deps(slug, by_slug):
            if is_stale(dep, by_slug, output_root, memo):
                to_run.add(dep)

    # Topo-sort the full DAG; filter to to_run (preserves dependency order
    # even when some intermediate nodes are fresh).
    full_order = _resolve_run_order(all_analyses)
    return [a for a in full_order if a["slug"] in to_run]


def stale_only(
    all_analyses: list[dict],
    output_root: Path,
) -> list[dict]:
    """Return all stale analyses in topo order."""
    by_slug = {a["slug"]: a for a in all_analyses}
    memo: dict[str, bool] = {}
    stale_slugs = {a["slug"] for a in all_analyses
                   if is_stale(a["slug"], by_slug, output_root, memo)}
    full_order = _resolve_run_order(all_analyses)
    return [a for a in full_order if a["slug"] in stale_slugs]


# ── Collection config loading ──────────────────────────────────────────


def _load_collection_config(analyses_root: Path) -> dict:
    """Load collection.yaml from the analyses directory, or return {}."""
    cfg = analyses_root / "collection.yaml"
    if cfg.exists():
        with open(cfg) as f:
            return yaml.safe_load(f) or {}
    return {}


# ── Execution ──────────────────────────────────────────────────────────


def run_analysis(analysis: dict, output_dir: Path) -> dict:
    """Execute a single analysis script. Returns result dict.

    `output_dir` here is the collection-level output root (e.g. _output/pilot1_test/);
    the slug-level output dir is output_dir / slug.
    """
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

    collection_config = _load_collection_config(analyses_root)

    result = {
        "slug": slug,
        "success": False,
        "stats": None,
        "error": None,
        "duration_s": 0,
    }

    t0 = time.time()
    try:
        _context.set_current(
            collection_dir=analyses_root,
            output_root=output_dir,
            output_dir=slug_output,
            collection_config=collection_config,
        )
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
        _context.clear()
        result["duration_s"] = round(time.time() - t0, 2)

    return result


def run_all(
    analyses_dir: Path,
    output_dir: Path,
    slugs: list[str] | None = None,
    *,
    only_stale: bool = False,
) -> list[dict]:
    """Discover and run analyses.

    - `slugs=None, only_stale=False` (default): force-run everything in topo order.
    - `slugs=None, only_stale=True`: run every stale analysis in topo order.
    - `slugs=[...], only_stale=False`: run those slugs AND any transitively-stale
      upstream of them, in topo order. (The slugs themselves always run.)
    - `slugs=[...], only_stale=True`: same as above, but targets only run if they
      are themselves stale.
    """
    all_analyses = discover_analyses(analyses_dir)

    if slugs:
        slug_set = set(slugs)
        known = {a["slug"] for a in all_analyses}
        missing = slug_set - known
        if missing:
            print(f"Warning: analyses not found: {', '.join(sorted(missing))}")
        to_run = expand_with_stale_upstream(list(slug_set & known), all_analyses, output_dir)
        if only_stale:
            by_slug = {a["slug"]: a for a in all_analyses}
            memo: dict[str, bool] = {}
            to_run = [a for a in to_run
                      if is_stale(a["slug"], by_slug, output_dir, memo)]
    else:
        if only_stale:
            to_run = stale_only(all_analyses, output_dir)
        else:
            to_run = _resolve_run_order(all_analyses)

    results = []
    target_set = set(slugs) if slugs else set()
    for a in to_run:
        note = "" if (not target_set or a["slug"] in target_set) else "  (upstream)"
        print(f"  Running {a['slug']}...{note}", end=" ", flush=True)
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
