"""Scaffold a new labdash analysis set."""

from pathlib import Path


def init_project(target: Path):
    """Create directory structure and starter files for a new analysis set."""
    analyses_dir = target / "analyses"
    lib_dir = analyses_dir / "_lib"
    example_dir = analyses_dir / "example_analysis"

    for d in [lib_dir, example_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # collection.yaml (collection-level config)
    config_path = analyses_dir / "collection.yaml"
    if not config_path.exists():
        config_path.write_text('''# LabDash collection configuration
title: "My Analyses"
# port: 8800                        # server port (default: 8800)
# output_dir: "_output"             # generated output (default: _output)
# publication_format: svg            # format for labdash export figures
# publication_dpi: 300
''')
        print(f"  Created {config_path}")

    # _lib/__init__.py
    init_path = lib_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text("")
        print(f"  Created {init_path}")

    # _lib/data_loading.py
    dl_path = lib_dir / "data_loading.py"
    if not dl_path.exists():
        dl_path.write_text('''"""Load and prepare data for analysis.

Edit this file to match your project's data format.
All analysis scripts import from here — this is the single
source of truth for data loading.

Data is cached in memory after the first load. When using
`labdash serve`, this means data loads once and subsequent
analysis runs reuse the cached data. Click "Reload Data"
in the viewer to clear the cache and reload from disk.
"""

import json
from pathlib import Path

import pandas as pd


# Path to data directory — adjust to match your project layout
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Module-level cache: data is loaded once and reused across analysis runs
_cache = None


def load_data() -> pd.DataFrame:
    """Load all data into a single DataFrame.

    Returns a copy of the cached DataFrame to prevent mutation.
    Modify this function to match your data format.
    """
    global _cache
    if _cache is not None:
        return _cache.copy()

    records = []
    data_dir = DATA_DIR
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    for f in sorted(data_dir.glob("*.json")):
        with open(f) as fh:
            doc = json.load(fh)
        # Customize: extract trials/rows from your data format
        if "trials" in doc:
            for trial in doc["trials"]:
                trial["participant_id"] = doc.get("participant_id", f.stem)
                records.append(trial)

    if not records:
        raise ValueError(f"No data found in {data_dir}")

    _cache = pd.DataFrame(records)
    return _cache.copy()


def clear_cache():
    """Clear the data cache. Next call to load_data() will reload from disk."""
    global _cache
    _cache = None
''')
        print(f"  Created {dl_path}")

    # _lib/style.py
    style_path = lib_dir / "style.py"
    if not style_path.exists():
        style_path.write_text('''"""Centralized aesthetic variables for all analyses.

Edit this file to change colors, fonts, and figure defaults
across all plots. All analysis scripts import from here.
"""

import matplotlib.pyplot as plt
import seaborn as sns


# ── Color palettes ───────────────────────────────────────
# Add your project-specific colors here
COLORS = {
    "primary": "#3498db",
    "secondary": "#2ecc71",
    "accent": "#e74c3c",
    "neutral": "#95a5a6",
}

# ── Figure defaults ──────────────────────────────────────
DEFAULT_DPI = 150
PUBLICATION_DPI = 300
DEFAULT_FIGSIZE = (8, 5)

# ── Font sizes ───────────────────────────────────────────
TITLE_SIZE = 14
LABEL_SIZE = 12
TICK_SIZE = 10


def apply_style():
    """Call at the start of every analysis to set consistent styling."""
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams.update({
        "figure.figsize": DEFAULT_FIGSIZE,
        "figure.dpi": DEFAULT_DPI,
        "axes.titlesize": TITLE_SIZE,
        "axes.labelsize": LABEL_SIZE,
        "xtick.labelsize": TICK_SIZE,
        "ytick.labelsize": TICK_SIZE,
        "savefig.dpi": DEFAULT_DPI,
        "savefig.bbox": "tight",
    })
''')
        print(f"  Created {style_path}")

    # _lib/preprocessing.py
    prep_path = lib_dir / "preprocessing.py"
    if not prep_path.exists():
        prep_path.write_text('''"""Common data transformations shared across analyses.

Add functions here for transforms used by multiple analysis scripts.
Keep analysis-specific logic in the analysis.py files themselves.
"""

import pandas as pd
''')
        print(f"  Created {prep_path}")

    # Example analysis
    meta_path = example_dir / "meta.yaml"
    if not meta_path.exists():
        meta_path.write_text('''title: "Example Analysis"
group: "Examples"
order: 1
description: >
  A simple example showing the labdash analysis contract.
  Replace this with your own analysis.
methodology: >
  This example loads data and creates a basic histogram.
  It demonstrates the standard analysis.py structure.
tags: [example]
status: draft
output_format: png
figure_id: ""
caption: ""
''')

    analysis_path = example_dir / "analysis.py"
    if not analysis_path.exists():
        analysis_path.write_text('''"""Example Analysis."""
import sys
from pathlib import Path

# Ensure _lib is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
from _lib.style import apply_style, COLORS

# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (8, 5)
TITLE = "Example: Random Data Histogram"
XLABEL = "Value"
YLABEL = "Count"
BAR_COLOR = COLORS["primary"]
N_SAMPLES = 1000
# ─────────────────────────────────────────────────────────


def run(output_dir: Path) -> dict:
    """Main entry point."""
    apply_style()

    # ── Analysis ──
    rng = np.random.default_rng(42)
    data = rng.normal(0, 1, N_SAMPLES)
    stats = {
        "mean": float(np.mean(data)),
        "std": float(np.std(data)),
        "n": N_SAMPLES,
    }

    # ── Figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.hist(data, bins=30, color=BAR_COLOR, edgecolor="white", alpha=0.8)
    ax.set_title(TITLE)
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(YLABEL)
    ax.axvline(stats["mean"], color=COLORS["accent"], linestyle="--",
               label=f"Mean = {stats['mean']:.2f}")
    ax.legend()

    # ── Save ──
    fig.savefig(output_dir / "output.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return stats


if __name__ == "__main__":
    run(Path(__file__).parent)
''')

    notes_path = example_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text("<!-- Your notes here. This file is never edited by AI agents. -->\n")

    # .gitignore for output
    gitignore_path = target / ".gitignore"
    ignore_line = "_output/"
    if gitignore_path.exists():
        content = gitignore_path.read_text()
        if ignore_line not in content:
            with open(gitignore_path, "a") as f:
                f.write(f"\n{ignore_line}\n")
    else:
        gitignore_path.write_text(f"{ignore_line}\n")

    print(f"\nLabDash collection initialized in {target}")
    print(f"  Edit analyses/_lib/data_loading.py to match your data format")
    print(f"  Edit analyses/_lib/style.py for your color scheme")
    print(f"  Edit analyses/collection.yaml to set title")
    print(f"  Run: labdash build -c analyses")
