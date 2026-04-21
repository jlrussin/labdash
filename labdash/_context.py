"""Runtime context for the currently-executing analysis.

The runner sets these before calling `mod.run(output_dir)` and clears
them after. Analysis scripts (and _lib/ helpers) can read them to resolve
upstream artifacts and data-source config without passing everything
through function arguments.

Outside of a labdash build/serve (e.g. `python analysis.py` run directly),
the globals stay None; callers should fall back to walking up from
`__file__`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# ── Globals ──────────────────────────────────────────────
# Directory of the currently-running collection (contains collection.yaml).
CURRENT_COLLECTION_DIR: Optional[Path] = None

# Output root for the current collection — one level above the current
# slug's output dir (e.g., _output/pilot1_test/).
CURRENT_OUTPUT_ROOT: Optional[Path] = None

# Output dir for the currently-running slug (e.g., _output/pilot1_test/sde_accuracy/).
CURRENT_OUTPUT_DIR: Optional[Path] = None

# Parsed collection.yaml for the current collection (data_dir, data_filter, etc.).
CURRENT_COLLECTION_CONFIG: Optional[dict] = None


def set_current(
    *,
    collection_dir: Path,
    output_root: Path,
    output_dir: Path,
    collection_config: dict,
) -> None:
    """Populate the runtime globals. Called by the runner before each analysis."""
    global CURRENT_COLLECTION_DIR, CURRENT_OUTPUT_ROOT
    global CURRENT_OUTPUT_DIR, CURRENT_COLLECTION_CONFIG
    CURRENT_COLLECTION_DIR = collection_dir
    CURRENT_OUTPUT_ROOT = output_root
    CURRENT_OUTPUT_DIR = output_dir
    CURRENT_COLLECTION_CONFIG = collection_config


def clear() -> None:
    """Reset the runtime globals. Called in the runner's finally block."""
    global CURRENT_COLLECTION_DIR, CURRENT_OUTPUT_ROOT
    global CURRENT_OUTPUT_DIR, CURRENT_COLLECTION_CONFIG
    CURRENT_COLLECTION_DIR = None
    CURRENT_OUTPUT_ROOT = None
    CURRENT_OUTPUT_DIR = None
    CURRENT_COLLECTION_CONFIG = None
