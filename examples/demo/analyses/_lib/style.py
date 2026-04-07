"""Centralized aesthetic variables for all analyses.

Edit colors, fonts, and figure defaults here. All analysis
scripts import from this module for consistent styling.
"""

import matplotlib.pyplot as plt
import seaborn as sns


# ── Color palettes ───────────────────────────────────────
COLORS = {
    "primary": "#3498db",
    "secondary": "#2ecc71",
    "accent": "#e74c3c",
    "neutral": "#95a5a6",
    "easy": "#2ecc71",
    "medium": "#f39c12",
    "hard": "#e74c3c",
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
