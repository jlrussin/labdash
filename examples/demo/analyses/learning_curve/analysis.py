"""Learning Curve — accuracy over trials with rolling average."""
import sys
from pathlib import Path

# Ensure _lib is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from _lib.data_loading import load_data
from _lib.style import apply_style, COLORS

# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (9, 5)
TITLE = "Learning Curve"
XLABEL = "Trial Index"
YLABEL = "Accuracy"
XLIM = None
YLIM = (-0.05, 1.15)
ROLLING_WINDOW = 5
SCATTER_SIZE = 60
SCATTER_ALPHA = 0.4
LINE_WIDTH = 2.5
LAYOUT_PAD = 2.0
# ─────────────────────────────────────────────────────────


def run(output_dir: Path) -> dict:
    """Main entry point."""
    apply_style()

    # ── Data ──
    df = load_data().sort_values("trial_index").reset_index(drop=True)
    correct = df["correct"].astype(int)
    trial_idx = df["trial_index"]
    rolling_acc = correct.rolling(window=ROLLING_WINDOW, min_periods=1).mean()

    overall_acc = float(correct.mean())
    n_trials = len(df)

    # ── Figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)

    # Individual trial outcomes
    ax.scatter(trial_idx, correct, s=SCATTER_SIZE, alpha=SCATTER_ALPHA,
               color=COLORS["neutral"], zorder=2, label="Trial outcome")

    # Rolling average
    ax.plot(trial_idx, rolling_acc, color=COLORS["primary"], linewidth=LINE_WIDTH,
            zorder=3, label=f"Rolling avg (w={ROLLING_WINDOW})")

    # Overall mean
    ax.axhline(overall_acc, color=COLORS["accent"], linestyle="--", linewidth=1.5,
               label=f"Overall = {overall_acc:.0%}")

    ax.set_title(TITLE)
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(YLABEL)
    if XLIM:
        ax.set_xlim(XLIM)
    if YLIM:
        ax.set_ylim(YLIM)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.legend(loc="lower right")
    fig.tight_layout(pad=LAYOUT_PAD)

    # ── Save ──
    fig.savefig(output_dir / "output.png")
    plt.close(fig)

    return {
        "overall_accuracy": round(overall_acc, 3),
        "n_trials": n_trials,
        "rolling_window": ROLLING_WINDOW,
    }


if __name__ == "__main__":
    run(Path(__file__).parent)
