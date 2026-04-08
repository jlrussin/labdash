---
name: labdash
description: >
  Use this skill whenever creating, editing, running, or managing analyses within a LabDash
  analysis set. Triggers include: "create an analysis," "add a plot," "labdash build," "labdash
  serve," working with files in an analyses/ directory that has _lib/ and meta.yaml files,
  editing analysis.py scripts that follow the LabDash contract (run(output_dir) function),
  discussing the LabDash dashboard or viewer, exporting figures for publication, or any
  task involving the labdash CLI tool. Also triggers when the user asks to "add a visualization"
  or "make a figure" in the context of a project that uses LabDash.
  For scientific best practices during analysis, also load the analysis skill.
---

# LabDash Skill

LabDash is an AI-agent-friendly analysis and visualization dashboard. Each analysis is a standalone Python script with metadata, producing a figure or table. A browser-based viewer displays results with descriptions, code, and user notes. A local server enables in-browser code editing and re-execution.

For scientific analysis best practices (statistical rigor, visualization philosophy, human-AI responsibilities), see the **analysis** skill. This skill covers LabDash tool mechanics only.

## 1. The analysis.py Contract

Every analysis script follows this exact structure:

```python
"""Short title matching meta.yaml title."""
import sys
from pathlib import Path

# Find _lib by walking up from script location
_d = Path(__file__).resolve().parent
while not (_d / "_lib").is_dir():
    _d = _d.parent
sys.path.insert(0, str(_d))

import matplotlib.pyplot as plt
from _lib.data_loading import load_data_function
from _lib.style import apply_style, RELEVANT_COLORS

# ── Aesthetic variables ──────────────────────────────────
# (Convention: grouped at top for easy human tweaking)
FIGSIZE = (8, 5)
TITLE = "Title Here"
XLABEL = "X Label"
YLABEL = "Y Label"
# ─────────────────────────────────────────────────────────


def run(output_dir: Path) -> dict:
    """Main entry point. Returns stats dict."""
    apply_style()
    df = load_data_function()

    # ── Analysis ──
    stats = {}

    # ── Figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)
    # ... plotting code ...

    # ── Save ──
    fig.savefig(output_dir / "output.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return stats


if __name__ == "__main__":
    run(Path(__file__).parent)
```

### Key conventions:

1. **Aesthetic variables at the top** in a clearly marked block between comment lines. Include: `FIGSIZE`, `TITLE`, labels, colors, `ALPHA`, marker sizes, line widths — anything the scientist might want to tweak visually. **Always include:**
   - **Layout/spacing**: `SUPTITLE_Y`, `TOP_MARGIN`, `HSPACE`, `WSPACE`, `LEGEND_LOC`, `LABEL_PAD`, `TITLE_PAD` — title/label overlap is the most common visual problem.
   - **Axis ranges**: `XLIM = None` and `YLIM = None` (or specific tuples like `(0, 1.05)`). When `None`, matplotlib auto-scales. The scientist can set explicit ranges without reading the plotting code.
   - **Shared axis toggle**: For multi-panel plots comparing test types with different RT scales, include `SHARE_Y_AXIS = False`. When False, each subplot gets its own y-range (prevents ICL's high RTs from compressing IWL's scale).

2. **`run(output_dir: Path) -> dict`** is the callable entry point. The runner imports and calls it. Returns a stats dict (saved as `stats.json` automatically).

3. **`if __name__ == "__main__":`** makes the script runnable standalone: `python analyses/slug/analysis.py`. Output goes to the script's own directory.

4. **Import from `_lib/`** for data loading, preprocessing, and style. Never write custom data loading in an individual analysis.

5. **One script = one output.** Each script produces exactly one figure (`output.png`) or table (`output.html`). If an analysis naturally has two views, make two analysis directories.

6. **Save the figure explicitly** with `fig.savefig(output_dir / "output.png", ...)` and `plt.close(fig)`.

## 2. File Structure

```
analysis/                            # Top-level analysis directory
├── _lib/                            # Shared code across all collections
│   ├── __init__.py
│   ├── data_loading.py              # Data loading functions
│   ├── preprocessing.py             # Common transforms
│   └── style.py                     # Colors, fonts, apply_style()
├── my_collection/                   # A "collection" — one set of analyses
│   ├── my_analysis/                 # One directory per analysis
│   │   ├── analysis.py              # The code
│   │   ├── meta.yaml                # Metadata (title, group, description, etc.)
│   │   └── notes.md                 # Scientist's annotations (NEVER EDIT)
│   ├── another_analysis/
│   │   └── ...
├── simulation/                      # Another collection (future)
│   └── ...
_output/                             # Generated (gitignored)
├── index.html                       # Static dashboard for active collection
├── my_analysis/
│   ├── output.png                   # Generated figure
│   └── stats.json                   # Generated statistics
labdash.yaml                         # Project config (analyses_dir points to active collection)
```

**Collections**: A collection is a subdirectory of `analysis/` containing a coherent set of analyses. The `_lib/` directory is shared across all collections. `labdash.yaml` points `analyses_dir` to the active collection (e.g., `analysis/my_collection`). Switch collections by changing this path.

## 3. The meta.yaml Format

```yaml
title: "Human-readable title"
group: "Group Name"              # Used for sidebar grouping in viewer
order: 1                         # Display order within group
description: >                   # Short description shown on card (1-2 sentences)
  What this analysis shows and why we care.
methodology: >                   # Detailed description (expandable in viewer)
  How the data was filtered, aggregated, and analyzed.
  Enough detail to understand the code without reading it.
  See methodology guidelines below.
tags: [accuracy, distance, sde]  # For search and filtering
status: draft                    # draft | active | publication
output_format: png               # png | svg | pdf | table
dependencies: []                 # Slugs of analyses that must run first
# Publication metadata (fill in when status: publication)
figure_id: ""                    # e.g., "Fig 2A"
caption: ""                      # Publication caption
```

### Status values:
- **draft** — exploration, may be incomplete or broken
- **active** — working analysis, part of ongoing investigation
- **publication** — finalized, has `figure_id` and `caption`, export-ready

### Methodology guidelines

The `methodology` field should let someone understand what the code does without reading it. Focus on **data and analysis logic**, not visual presentation.

**Include:**
- Where the data comes from and how it's filtered (which phases, which participants, exclusion criteria)
- How values are aggregated (mean, median, per-participant, etc.)
- What statistical tests are used, if any
- What the axes represent and what comparisons are being made
- How between-subjects factors are handled (split by condition)

**Do NOT include:**
- Specific aesthetic values that live in the code (number of bins, line styles, colors, figure sizes, bar widths)
- Visual formatting details ("dashed line", "horizontal line", "15 bins")
- Anything the user might change by editing the aesthetic variables block

**Why:** The methodology description must stay accurate even when someone tweaks the aesthetic variables at the top of `analysis.py`. If the methodology says "15 bins" and the user changes `N_BINS` to 20, the description becomes wrong. Describe *what* is shown (e.g., "median RT is marked"), not *how* it's styled (e.g., "a dashed vertical line marks the median").

**Good example:**
> For each phase, we plot a histogram of response times (ms) with the median RT marked. Trials with missing RT values (e.g., timeouts) are excluded. Each condition is plotted separately.

**Bad example:**
> For each phase, we plot a histogram with 15 bins and a dashed vertical line at the median RT. Bar color is set per phase. Figure size is 16x3.5.

## 4. Agent Rules

### NEVER edit `notes.md` files
These are the scientist's personal annotations. They appear in the viewer but are exclusively human-authored. If you need to add information to a card, update `meta.yaml` (description or methodology fields).

### ALWAYS read `_lib/style.py` before creating plots
Use existing color palettes and the `apply_style()` function. Don't define new colors unless the existing palette doesn't cover the need.

### ALWAYS read `_lib/data_loading.py` before loading data
Use the existing functions. Don't write custom data loading in analysis scripts.

### ALWAYS run after editing
After creating or modifying an `analysis.py`, run it to verify:
```bash
python analyses/my_analysis/analysis.py
```
Then verify the output image looks correct by reading it.

### ALWAYS update meta.yaml when changing what an analysis does
If you change what a script computes or visualizes, update the `description` and `methodology` fields to match.

## 5. CLI Commands

```bash
# Build (run analyses + generate static HTML dashboard)
labdash build                        # run all analyses in default collection
labdash build slug1 slug2            # run specific analyses only
labdash build -c path/to/collection  # build a specific collection

# Serve (live development server with in-browser editing)
labdash serve                        # starts on localhost:8800
labdash serve --port 9000            # custom port
labdash serve -c path/to/collection  # serve a specific collection

# Export (publication-ready output)
labdash export figures               # SVG figures for publication
labdash export figures --format pdf  # PDF format
labdash export figures --status active  # export non-publication analyses
labdash export code                  # self-contained code directory

# Initialize (scaffold new project)
labdash init /path/to/project        # create directory structure
```

### Build vs. Serve

**Build is persistent** — once a collection has been built, the output files exist in `_output/` and don't need rebuilding unless an `analysis.py` script changes. The serve command reads from existing outputs. **Do not rebuild a collection from scratch** just to view it. Only rebuild individual analyses that have changed:

```bash
labdash build -c my_collection changed_analysis  # rebuild just one
labdash serve -c my_collection                   # view all (uses cached outputs)
```

### Running Multiple Collections Simultaneously

Use `--port` to serve multiple collections at once:

```bash
labdash serve -c analyses/pilot1 --port 8800
labdash serve -c analyses/simulation --port 8801
```

## 6. Creating a New Analysis

Step-by-step workflow:

1. **Create the directory:**
   ```bash
   mkdir analyses/my_new_analysis
   ```

2. **Write `meta.yaml`** with title, group, description, methodology, tags, status.

3. **Write `analysis.py`** following the contract. Import from `_lib/`. Aesthetic variables at top.

4. **Create `notes.md`** with the placeholder comment:
   ```markdown
   <!-- Your notes here. This file is never edited by AI agents. -->
   ```

5. **Run the script** to verify it works:
   ```bash
   python analyses/my_new_analysis/analysis.py
   ```

6. **Read the output image** to verify it looks correct.

7. **Build the dashboard** to see it in context:
   ```bash
   labdash build my_new_analysis
   ```

## 7. Table Output

For statistical tests that don't naturally produce a figure, use `output_format: table` in meta.yaml. The `run()` function should return a dict with a `"table"` key:

```python
def run(output_dir: Path) -> dict:
    apply_style()
    df = load_data()

    # ... compute statistics ...

    stats = {
        "table": [
            {"Test": "t-test", "Statistic": 2.35, "p": 0.023, "d": 0.67},
            {"Test": "Mann-Whitney", "Statistic": 145, "p": 0.031, "r": 0.42},
        ],
        "summary": "Both tests significant at alpha = .05"
    }

    # Render table as HTML
    table_df = pd.DataFrame(stats["table"])
    html = table_df.to_html(index=False, classes="stats-table")
    (output_dir / "output.html").write_text(html)

    return stats
```

## 8. Working with `_lib/`

### data_loading.py
- Contains functions like `load_all_participants()`, `build_trial_dataframe()`
- Returns DataFrames with a `participant_id` column for multi-participant analysis
- Handles JSON parsing, type conversions, filtering

### preprocessing.py
- Contains shared transforms used by multiple analyses
- Functions here are used by multiple analyses
- Keep analysis-specific logic in the analysis scripts themselves

### style.py
- `apply_style()` — call at the start of every analysis
- `PHASE_COLORS`, `CONDITION_COLORS` — use for consistent coloring
- `phase_color(phase)` — helper that works with both full and short phase names
- `DEFAULT_DPI`, `DEFAULT_FIGSIZE` — shared defaults

## 9. Handling Dependencies

If analysis B needs statistics computed by analysis A:

1. A's `meta.yaml`: no special config needed
2. B's `meta.yaml`: add `dependencies: [a_slug]`
3. A's `run()` returns the stats dict (saved automatically as `stats.json`)
4. B's `run()` loads them:
   ```python
   with open(Path(__file__).parent.parent / "a_slug" / "stats.json") as f:
       a_stats = json.load(f)
   ```

The runner topologically sorts analyses to respect dependencies.

## 10. Tag Discipline

Be conservative with tags. Tags are for filtering and discovery, not for describing everything about an analysis.

**Guidelines:**
- Aim for **5-15 unique tags** across a collection (recommendation, not hard rule)
- Use tags that correspond to the data subsets or analysis types in your project
- Avoid redundant tags — if two tags always co-occur, one is unnecessary
- Don't add tags speculatively — add them when you have a filtering use case
- Check the `registry.yaml` file before creating new tags to see what already exists

## 11. Agent Notes (`agent_notes.md`)

Each analysis directory may contain an `agent_notes.md` file — short, concise notes written by the agent about changes the user requested, edge cases discovered, or gotchas to avoid.

**Purpose:** Helps future sessions avoid repeating past mistakes. When the user asks for a change and the agent learns something non-obvious, it records it here.

**Two levels:**
- **Per-analysis** `agent_notes.md` — in each analysis directory, for analysis-specific gotchas
- **Collection-level** `agent_notes.md` — in the collection root (e.g., `analysis/my_collection/agent_notes.md`), for cross-cutting lessons that apply to all analyses

**Rules:**
- Keep it brief — bullet points, not prose
- Write whenever the user requests a change or you discover a non-obvious bug
- **Read the collection-level notes before creating or rebuilding any analysis**
- Read per-analysis notes before editing that specific analysis
- `notes.md` is still human-only; `agent_notes.md` is agent-authored
- Agent notes are visible in the viewer as a read-only "Agent Notes" section on cards

**Example:**
```markdown
- User changed N_BINS from 15 to 7 for histograms (too many bins for small N)
- seaborn boxplot auto-sets ylabel from column name — always set explicitly
- Data loader returns abbreviated keys ("train" not "training") — use correct keys
```

## 12. Change Log (`change_log.md`)

The collection-level `change_log.md` is an **append-only log** of edits the user makes via the viewer (code saves, metadata changes, notes edits). Each entry has a timestamp, the affected analysis slug, the type of change, and a diff or summary.

**Purpose:** Agents should read this before creating or editing analyses to learn the user's patterns and preferences. For example, if the log shows the user consistently changes `fontsize=14` to `fontsize=16`, the agent should use 16 by default in new analyses.

**Rules:**
- **Read before creating new analyses** — look for patterns in how the user edits existing ones
- Never edit or delete the change log
- The log is auto-generated by the server; agents don't write to it

## 13. Registry (`registry.yaml`)

Auto-generated file listing all groups and tags in the collection. Updated on every `labdash build` and every metadata edit via the server.

**Purpose:** Agents should read this before creating new analyses to see what groups and tags already exist, and choose from them rather than inventing new ones.

```yaml
groups:
  Performance:
  - accuracy_by_phase
  - rt_by_distance_combined
  Symbolic Distance:
  - sde_accuracy
  - sde_rt
tags:
- accuracy
- arbitrage
- icl
- rt
- training
```

**Rules:**
- Read `registry.yaml` before assigning groups and tags to new analyses
- Prefer existing groups and tags over creating new ones
- Never edit manually — it's regenerated automatically

## 14. Staleness Detection

`labdash build` compares `analysis.py` modification time against `output.png` modification time. Stale outputs (code newer than output) are flagged with a "stale" badge in the viewer. This helps the scientist know which results are current.
