"""R-side analysis runner (subprocess via `Rscript`).

LabDash itself is a Python program, so R analyses are run as child
processes rather than in-process. One `Rscript --vanilla <bootstrap>
<analysis.R> <slug_output>` invocation per analysis. The bootstrap
script (bundled at `r_bootstrap/run_analysis.R`) sources the user's
`analysis.R`, calls `run(output_dir)`, and serialises the returned
list to `stats.json`. Runtime context flows from labdash to R through
environment variables (`LABDASH_OUTPUT_ROOT`, `LABDASH_OUTPUT_DIR`,
`LABDASH_COLLECTION_DIR`, `LABDASH_LIB_DIR`, `LABDASH_COLLECTION_CONFIG`).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


# Tail of stderr to include in error messages (keeps the failure
# diagnosable without spamming the dashboard with the entire R
# traceback).
_STDERR_TAIL_LINES = 60


def _bootstrap_path() -> Path:
    """Absolute path to the bundled R bootstrap script."""
    return Path(__file__).resolve().parent / "r_bootstrap" / "run_analysis.R"


def _resolve_lib_dir(analyses_root: Path) -> Path | None:
    """Mirror builder._resolve_lib_dir without importing it (avoid cycles).

    `_lib/` may live inside the collection dir or one level up.
    """
    for d in [analyses_root, analyses_root.parent]:
        candidate = d / "_lib"
        if candidate.is_dir():
            return candidate
    return None


def run(analysis: dict, output_dir: Path) -> dict:
    """Execute an R analysis. Returns the standard result dict.

    `output_dir` is the collection-level output root; the slug's own
    directory `output_dir / slug` is created here.

    The R process's exit code determines `success`. On non-zero exit
    the last `_STDERR_TAIL_LINES` lines of stderr land in `error`.
    """
    slug = analysis["slug"]
    analysis_path = analysis["analysis_path"]
    analyses_root = analysis_path.parent.parent
    slug_output = output_dir / slug
    slug_output.mkdir(parents=True, exist_ok=True)

    lib_dir = _resolve_lib_dir(analyses_root)
    collection_yaml = analyses_root / "collection.yaml"

    env = os.environ.copy()
    env["LABDASH_COLLECTION_DIR"] = str(analyses_root)
    env["LABDASH_OUTPUT_ROOT"] = str(output_dir)
    env["LABDASH_OUTPUT_DIR"] = str(slug_output)
    env["LABDASH_COLLECTION_CONFIG"] = str(collection_yaml) if collection_yaml.exists() else ""
    env["LABDASH_LIB_DIR"] = str(lib_dir) if lib_dir is not None else ""
    env["LABDASH_SLUG"] = slug
    # Quiet startup banner / messages — keeps stderr signal-to-noise high.
    env.setdefault("R_DEFAULT_PACKAGES", "datasets,utils,grDevices,graphics,stats,methods")

    bootstrap = _bootstrap_path()

    result = {
        "slug": slug,
        "success": False,
        "stats": None,
        "error": None,
        "duration_s": 0,
    }

    t0 = time.time()
    try:
        # Note: we deliberately do NOT pass `--vanilla` here. That flag
        # implies `--no-environ`, which skips `.Renviron` and therefore
        # `R_LIBS_USER` — users with packages in their personal library
        # would see "package not found" failures even when the packages
        # are installed. By default Rscript already doesn't save / restore
        # the workspace, so the default invocation is the right balance:
        # full access to the user's installed packages + a clean run.
        proc = subprocess.run(
            ["Rscript", str(bootstrap), str(analysis_path)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        result["error"] = (
            "Rscript was not found on PATH. Install R (https://cran.r-project.org/) "
            "and ensure `Rscript` is on PATH before running R collections."
        )
        result["duration_s"] = round(time.time() - t0, 2)
        return result

    result["duration_s"] = round(time.time() - t0, 2)

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().splitlines()
        tail = "\n".join(stderr[-_STDERR_TAIL_LINES:])
        result["error"] = tail or f"Rscript exited with code {proc.returncode}."
        return result

    # Bootstrap is responsible for writing stats.json. If it's missing,
    # the analysis succeeded but produced no stats — treat as empty.
    stats_path = slug_output / "stats.json"
    if stats_path.exists():
        try:
            result["stats"] = json.loads(stats_path.read_text())
        except json.JSONDecodeError as e:
            result["error"] = (
                f"R analysis succeeded but stats.json is invalid JSON: {e}. "
                f"Check that `run()` returns a list serialisable via "
                f"`jsonlite::toJSON`."
            )
            return result
    else:
        result["stats"] = {}

    result["success"] = True
    return result
