"""RT Distribution — histogram of reaction times with median line."""
import sys
from pathlib import Path

# Ensure _lib is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
from _lib.data_loading import load_data
from _lib.style import apply_style, COLORS

# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (8, 5)
TITLE = "Reaction Time Distribution"
XLABEL = "RT (ms)"
YLABEL = "Count"
XLIM = (200, 900)
YLIM = None
BAR_COLOR = COLORS["primary"]
MEDIAN_COLOR = COLORS["accent"]
N_BINS = 15
LAYOUT_PAD = 2.0
# ─────────────────────────────────────────────────────────


def run(output_dir: Path) -> dict:
    """Main entry point."""
    apply_style()

    # ── Data ──
    df = load_data()
    rt = df["rt"].values

    median_rt = float(np.median(rt))
    mean_rt = float(np.mean(rt))
    std_rt = float(np.std(rt))

    # ── Figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.hist(rt, bins=N_BINS, color=BAR_COLOR, edgecolor="white", alpha=0.85)
    ax.axvline(median_rt, color=MEDIAN_COLOR, linestyle="--", linewidth=2,
               label=f"Median = {median_rt:.0f} ms")
    ax.set_title(TITLE)
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(YLABEL)
    if XLIM:
        ax.set_xlim(XLIM)
    if YLIM:
        ax.set_ylim(YLIM)
    ax.legend()
    fig.tight_layout(pad=LAYOUT_PAD)

    # ── Save ──
    fig.savefig(output_dir / "output.png")
    plt.close(fig)

    return {
        "median_rt_ms": median_rt,
        "mean_rt_ms": round(mean_rt, 1),
        "std_rt_ms": round(std_rt, 1),
        "n_trials": len(rt),
    }


if __name__ == "__main__":
    run(Path(__file__).parent)
