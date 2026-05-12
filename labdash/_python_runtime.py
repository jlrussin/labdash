"""Python-specific analysis runner.

Extracted from `runner.py` so that the language-agnostic runner can
dispatch through `languages.PythonLanguage.run_analysis` instead of
calling `importlib` directly. Behaviour is unchanged from the
pre-language-adapter implementation: the analysis module is loaded in-
process via `importlib`, `run(output_dir)` is invoked, and the returned
stats dict is serialised to `stats.json`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
import yaml
from pathlib import Path

from . import _context


def _load_collection_config(analyses_root: Path) -> dict:
    cfg = analyses_root / "collection.yaml"
    if cfg.exists():
        with open(cfg) as f:
            return yaml.safe_load(f) or {}
    return {}


def run(analysis: dict, output_dir: Path) -> dict:
    """Execute a Python analysis. Returns the standard result dict.

    `output_dir` is the collection-level output root; the slug's own
    directory is `output_dir / slug` and is created here.
    """
    slug = analysis["slug"]
    analysis_path = analysis["analysis_path"]
    analyses_root = analysis_path.parent.parent
    slug_output = output_dir / slug
    slug_output.mkdir(parents=True, exist_ok=True)

    # sys.path needs analyses_root or its parent (whichever contains _lib/)
    # so `from _lib.X import Y` resolves in the loaded module.
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
        spec = importlib.util.spec_from_file_location(
            f"analysis_{slug}", str(analysis_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        stats = mod.run(slug_output)
        result["success"] = True
        result["stats"] = stats or {}
        if stats:
            with open(slug_output / "stats.json", "w") as f:
                json.dump(stats, f, indent=2, default=str)
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        _context.clear()
        result["duration_s"] = round(time.time() - t0, 2)

    return result
