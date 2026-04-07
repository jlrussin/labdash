"""Accuracy by Difficulty — bar chart of accuracy across difficulty levels."""
import sys
from pathlib import Path

# Ensure _lib is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
from _lib.data_loading import load_data
from _lib.style import apply_style, COLORS

# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (7, 5)
TITLE = "Accuracy by Difficulty Level"
XLABEL = "Difficulty"
YLABEL = "Accuracy"
XLIM = None
YLIM = (0, 1.05)
BAR_WIDTH = 0.5
DIFFICULTY_ORDER = ["easy", "medium", "hard"]
DIFFICULTY_COLORS = [COLORS["easy"], COLORS["medium"], COLORS["hard"]]
LAYOUT_PAD = 2.0
# ─────────────────────────────────────────────────────────


def _binomial_ci(k, n, z=1.96):
    """Wilson score 95% confidence interval for a proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    spread = z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
    return max(0, center - spread), min(1, center + spread)


def run(output_dir: Path) -> dict:
    """Main entry point."""
    apply_style()

    # ── Data ──
    df = load_data()
    stats = {}
    accuracies = []
    ci_lower = []
    ci_upper = []

    for diff in DIFFICULTY_ORDER:
        subset = df[df["difficulty"] == diff]
        n = len(subset)
        k = int(subset["correct"].sum())
        acc = k / n if n > 0 else 0.0
        lo, hi = _binomial_ci(k, n)
        accuracies.append(acc)
        ci_lower.append(acc - lo)
        ci_upper.append(hi - acc)
        stats[f"accuracy_{diff}"] = round(acc, 3)
        stats[f"n_{diff}"] = n

    # ── Figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = np.arange(len(DIFFICULTY_ORDER))
    bars = ax.bar(x, accuracies, width=BAR_WIDTH, color=DIFFICULTY_COLORS,
                  edgecolor="white", alpha=0.85,
                  yerr=[ci_lower, ci_upper], capsize=5, error_kw={"linewidth": 1.5})
    ax.set_xticks(x)
    ax.set_xticklabels([d.capitalize() for d in DIFFICULTY_ORDER])
    ax.set_title(TITLE)
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(YLABEL)
    if YLIM:
        ax.set_ylim(YLIM)
    ax.axhline(0.5, color=COLORS["neutral"], linestyle=":", linewidth=1, label="Chance")
    ax.legend()
    fig.tight_layout(pad=LAYOUT_PAD)

    # ── Save ──
    fig.savefig(output_dir / "output.png")
    plt.close(fig)

    return stats


if __name__ == "__main__":
    run(Path(__file__).parent)
