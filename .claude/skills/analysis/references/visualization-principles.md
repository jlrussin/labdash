# Visualization Principles Reference

## Core Rules

### 1. Show the data, not just summaries
- Overlay individual data points on bar charts and box plots
- Use `stripplot` or `swarmplot` alongside `barplot` or `boxplot`
- For small N, every data point should be visible
- For large N, use transparency (alpha) or density-based methods

### 2. Encode information efficiently
- Position (x/y axes) for the most important comparison
- Color for categorical grouping (condition, phase)
- Shape/style for secondary grouping (avoid overloading)
- Size only when it encodes a continuous variable (bubble charts)
- Never use 3D charts — they distort perception of values

### 3. Make comparisons easy
- When comparing groups, put them side by side (not in separate panels)
- When comparing across conditions, use the same axis limits
- Use consistent colors for the same variable across all plots
- Align related plots horizontally so the reader's eye can scan

### 4. Label everything
- Axis labels with units: "Response Time (ms)" not "RT"
- Title that describes the finding, not the method: "Accuracy Increases with Distance" not "Accuracy by Distance Plot"
- Legend should be inside the plot when possible (saves space, easier to read)
- Include N in title, subtitle, or legend: "(N = 42 per condition)"

### 5. Bar chart tick rules
- **One tick per bar** (vertical) or one tick per bar (horizontal). Never let matplotlib auto-generate intermediate ticks on a categorical/discrete axis.
- For vertical bar charts: always call `ax.set_xticks()` with the exact bar positions and `ax.set_xticklabels()` with the category labels.
- For histograms of discrete integer data (e.g., context hops 1-6): set xticks to the integer values explicitly with `ax.set_xticks(sorted_unique_values)`. Matplotlib's default continuous axis will otherwise show fractional ticks like 1.5, 2.5.
- This also applies to grouped/stacked bars — ticks should align with group centers.

### 6. Be honest
- Start y-axis at 0 for bar charts (proportions, counts)
- Start y-axis at a meaningful value for line plots (the trend matters more than the baseline)
- Don't truncate axes to exaggerate effects
- If error bars are present, state what they represent (SE, SD, 95% CI)
- Use symmetric error bars for symmetric measures; asymmetric for proportions

## Plot Type Selection

| Data pattern | Recommended plot | When to use |
|-------------|-----------------|-------------|
| Distribution of one variable | Histogram or KDE | Checking normality, identifying outliers |
| Comparing group means | Bar plot + individual points | Few groups, continuous DV |
| Comparing group distributions | Box plot or violin + strip | Showing spread, skew, outliers |
| Trend over ordered levels | Line plot | Ordinal x-axis (distance, block number) |
| Two continuous variables | Scatter plot | Checking correlation, identifying patterns |
| Pairwise performance | Heatmap | Item-by-item matrices (accuracy, RT) |
| Proportions across categories | Stacked or grouped bar | Categorical DV |
| Time series or learning | Line plot with CI band | Block-by-block performance |
| Interaction effects | Line plot with separate lines per group | Condition × factor interactions |

## Table Output Conventions

When building HTML tables for analysis output:

- **Structure data as one row per observation** — don't spread related fields across columns where most cells end up NaN. For example, questionnaire data should be Question × Response rows, not one column per question.
- **Filter out internal/bookkeeping fields** (e.g., `progress_*`, `trial_index`) — only show columns meaningful to the scientist.
- **Headers should not wrap** — keep column names short and set `white-space: nowrap` on `th`.
- **Cell content must wrap** — use `word-wrap: break-word` on `td` so long text (free-text responses, descriptions) stays inside the cell rather than overflowing.
- **Cap column width** — set `max-width` on `td` (e.g., 500px) to prevent one long-text column from dominating the table.
- When using pandas `.style.set_table_styles()`, the viewer's global CSS handles wrapping, so don't add conflicting `white-space: nowrap` to `td` in the inline styles.

## Color Conventions

### Accessibility
- Use colorblind-friendly palettes by default (avoid pure red-green distinctions)
- Good defaults: seaborn's `colorblind` palette, or manually chosen sets
- Test with a colorblindness simulator if the paper may be printed in grayscale

### Consistency
- Define colors once in a shared style file; import everywhere
- Same condition = same color across all plots in a paper
- Same phase = same color across all plots
- Don't use color alone to convey information — pair with shape or pattern

### Semantic conventions
- Red/warm = error, violation, conflict
- Green/cool = correct, expected
- Gray = chance, baseline, reference
- High saturation = important; low saturation = background

## Figure Sizing

### For exploration
- Use `figsize=(8, 5)` or `(10, 6)` for comfortable viewing
- Wider for multi-panel: `(14, 5)` for 3 panels side by side

### For publication
- Single-column: 3.5 inches wide (88 mm)
- Double-column: 7.0 inches wide (178 mm)
- Max height: 9.0 inches (229 mm)
- Use vector formats (SVG, PDF) for publication
- Font size in the saved figure must be readable at print size (~8-10pt minimum)

## Aesthetic Variables Convention

When writing analysis scripts, group aesthetic variables at the top of the file in a clearly marked block. This makes it easy for the scientist to find and tweak visual properties without reading the analysis logic.

```python
# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (8, 5)
TITLE = "Accuracy by Phase"
XLABEL = "Phase"
YLABEL = "Proportion Correct"
BAR_COLOR = "#3498db"
BAR_WIDTH = 0.6
ALPHA = 0.8
ERROR_BAR_CAPSIZE = 3
# Layout spacing (adjust if titles/labels overlap)
SUPTITLE_Y = 1.02
TOP_MARGIN = 0.92
HSPACE = 0.3
WSPACE = 0.3
LEGEND_LOC = "best"
TITLE_PAD = 12
LABEL_PAD = 8
# ─────────────────────────────────────────────────────────
```

Common aesthetic variables to expose:
- `FIGSIZE` — overall figure dimensions
- `TITLE`, `XLABEL`, `YLABEL` — text labels
- Colors for each group/condition
- `ALPHA` — transparency for overlapping elements
- Marker size, line width for line plots
- Bar width, error bar capsize for bar plots
- Font sizes (if overriding the shared style)

**Layout/spacing variables (always include these):**
- `SUPTITLE_Y` — vertical position of `plt.suptitle()` (default 1.0; use >1.0 to push above figure)
- `TOP_MARGIN` — `fig.subplots_adjust(top=...)` to make room for suptitle
- `HSPACE`, `WSPACE` — vertical and horizontal spacing between subplots
- `LEGEND_LOC` — legend position string or tuple
- `TITLE_PAD` — padding between subplot title and axes
- `LABEL_PAD` — padding between axis labels and tick labels

Title/label overlap is the most common visual problem in multi-panel figures. Making these variables easily tweakable saves significant iteration time.

## Multi-Panel Figures

### Layout principles
- Use `plt.subplots()` with `sharex`/`sharey` when panels share an axis
- Label shared axes once, not on every panel
- Use `plt.suptitle()` for the overall title, `ax.set_title()` for panel titles
- Consistent spacing with `plt.tight_layout()` or `fig.subplots_adjust()`

### When to facet vs. overlay
- **Facet** (separate panels): when groups have different y-scales, or >3 groups would clutter one panel
- **Overlay** (same panel): when the comparison between groups is the main point, and ≤3 groups
- **Facet by condition, overlay by test type**: common pattern for between × within designs
