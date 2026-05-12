"""Discovers and executes analysis scripts.

Language-agnostic: the per-collection `language:` field in
`collection.yaml` selects a `labdash.languages.Language` adapter that
knows the script filename, the import-parser, and the execution
strategy. Python keeps in-process import semantics
(see `_python_runtime.py`); R shells out to `Rscript`
(see `r_runner.py`).

The runner cares about discovery, topological ordering, and staleness;
the adapter handles the language-specific bits.
"""

import math
from pathlib import Path

import yaml

from .languages import Language, resolve_language_for


def discover_analyses(
    analyses_dir: Path, *, language: Language | None = None
) -> list[dict]:
    """Find all analysis directories containing the right script + meta.yaml.

    `language` defaults to whatever `collection.yaml`'s `language:` field
    resolves to (Python if absent). The discovered script filename
    follows the adapter (`analysis.py` for Python, `analysis.R` for R).

    Returns list of dicts with keys: slug, dir, meta, analysis_path,
    language. Sorted by (group, order, slug) as a fallback ordering.
    """
    lang = language or resolve_language_for(analyses_dir)
    analyses = []
    for d in sorted(analyses_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        analysis_path = d / lang.analysis_filename
        meta_yaml = d / "meta.yaml"
        if not analysis_path.exists() or not meta_yaml.exists():
            continue
        with open(meta_yaml) as f:
            meta = yaml.safe_load(f)
        if meta.get("status") == "disabled":
            continue
        analyses.append({
            "slug": d.name,
            "dir": d,
            "meta": meta,
            "analysis_path": analysis_path,
            "language": lang,
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
    - self-stale if the wrapper OR any imported _lib/ file is newer than
      the oldest of its output files (or if its output dir is empty);
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

    # Self-stale: wrapper source newer than my oldest output.
    if analysis["analysis_path"].stat().st_mtime > my_oldest:
        _memo[slug] = True
        return True

    # Self-stale via shared module: any imported _lib/ file newer than output.
    # `shared_paths` (attached by builder) is a list of existing _lib/-relative
    # paths resolved against the analyses_dir's _lib/ directory. Fall back to
    # the analysis_path's grandparent-heuristic if builder didn't set it.
    for shared_abs in _shared_paths_abs(analysis):
        try:
            if shared_abs.stat().st_mtime > my_oldest:
                _memo[slug] = True
                return True
        except FileNotFoundError:
            continue

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


def _shared_paths_abs(analysis: dict) -> list[Path]:
    """Absolute paths of `_lib/` files this wrapper TRANSITIVELY imports.

    Prefers `analysis["shared_paths"]` (list of `_lib/`-relative strings,
    normally attached by the builder as the transitive closure). Falls back
    to building the import graph and computing the closure on demand so pure
    runner paths (e.g. `labdash build --only-stale` without a preceding
    builder pass) still propagate shared-file edits correctly.
    """
    # analyses_dir = analysis script's grandparent (collection root).
    analyses_dir = analysis["analysis_path"].parent.parent
    lib_dir: Path | None = None
    for d in [analyses_dir, analyses_dir.parent]:
        if (d / "_lib").is_dir():
            lib_dir = d / "_lib"
            break
    if lib_dir is None:
        return []

    rels = analysis.get("shared_paths")
    if rels is None:
        from .lib_graph import build_lib_graph, transitive_lib_closure
        lang = analysis.get("language") or resolve_language_for(analyses_dir)
        graph = build_lib_graph(lib_dir, language=lang)
        direct = lang.parse_lib_imports(analysis["analysis_path"], lib_dir)
        rels = sorted(transitive_lib_closure(direct, graph))
        analysis["shared_paths"] = rels  # cache back

    return [lib_dir / rel for rel in rels]


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


# ── Execution ──────────────────────────────────────────────────────────


def run_analysis(analysis: dict, output_dir: Path) -> dict:
    """Execute a single analysis. Dispatches through the language adapter.

    `output_dir` is the collection-level output root (e.g. `_output/exp1/`);
    the slug-level directory `output_dir / slug` is created by the adapter.
    Returns the standard result dict: {slug, success, stats, error, duration_s}.
    """
    lang: Language = analysis.get("language")
    if lang is None:
        # Should not happen if `discover_analyses` was used, but be lenient
        # for callers that hand-build analysis dicts (older tests).
        analyses_dir = analysis["analysis_path"].parent.parent
        lang = resolve_language_for(analyses_dir)
        analysis["language"] = lang
    return lang.run_analysis(analysis, output_dir)


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
