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

from _lib.style import apply_style, RELEVANT_COLORS  # import before pyplot (sets agg backend)
import matplotlib.pyplot as plt
from _lib.data_loading import load_data_function

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
analysis/
├── _lib/                             # Shared across all collections
│   ├── data_loading.py               # Reads data_dir/data_filter from active collection.yaml
│   ├── preprocessing.py              # Common transforms
│   ├── style.py                      # Colors, fonts, apply_style()
│   ├── pipeline.py                   # upstream()/load_pickle()/load_json() artifact helper
│   └── plots/                        # Shared plot functions: make(df, output_dir, **kwargs)
├── my_collection/                    # Self-contained: own data source + pipeline + leaves
│   ├── collection.yaml               # data_dir, data_filter, title
│   ├── registry.yaml                 # auto-synced: group order + tags
│   ├── build_trials_df/              # Pipeline node (output_format: pipeline)
│   └── my_analysis/                  # Leaf analysis
│       ├── analysis.py
│       ├── meta.yaml
│       └── notes.md                  # Scientist's annotations (NEVER EDIT)
├── another_collection/               # Another collection — different data source
│   └── ...
_output/                              # Generated (gitignored)
├── my_collection/                    # Per-collection subtree (no cross-collection collisions)
│   ├── index.html
│   ├── build_trials_df/
│   │   └── trials.pkl                # Artifact for downstream leaves to load
│   └── my_analysis/
│       ├── output.png
│       └── stats.json
```

**Collections.** Each collection is self-contained: it owns its `collection.yaml` (data source + title), its pipeline nodes, its leaf analyses, and its own `_output/<collection>/` subtree. No project-level `labdash.yaml` — data source config lives per-collection. Switch collections with `-c <collection_dir>` on any CLI command.

## 3. The meta.yaml Format

```yaml
title: "Human-readable title"
group: "Group Name"              # Default group for new analyses (registry is source of truth)
description: >                   # Short description shown on card (1-2 sentences)
  What this analysis shows and why we care.
methodology: >                   # Detailed description (expandable in viewer)
  How the data was filtered, aggregated, and analyzed.
  Enough detail to understand the code without reading it.
  See methodology guidelines below.
tags: [accuracy, distance, sde]  # For search and filtering
status: draft                    # draft | active | publication
output_format: png               # png | svg | table | pipeline
dependencies: [upstream_slug]    # Slugs that must run first (staleness propagates transitively)
# Publication metadata (fill in when status: publication)
figure_id: ""                    # e.g., "Fig 2A"
caption: ""                      # Publication caption
```

**`output_format: pipeline`** marks a transform node that emits data artifacts (parquet/pickle/JSON) instead of figures. The card still appears in the dashboard — it shows description/methodology/code/stats/tags plus a "pipeline" badge — but the "No output yet" placeholder is suppressed. A pipeline node may ALSO emit `output.png` (e.g., a histogram of what was excluded) and the viewer will display it.

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

## 4.5 Pipelines, artifact helper, and transitive staleness

Analyses form a DAG via `dependencies:` in `meta.yaml`. Staleness propagates transitively — touching any upstream source marks all downstream analyses as stale. Clicking **Run** on a leaf (or invoking `labdash build <leaf>`) auto-includes transitively-stale upstream in topo order; there are no warnings, a Run always means run.

**Pipeline nodes** (`output_format: pipeline`) are transforms whose job is to emit derived artifacts (pickled DataFrames, exclusion JSON lists, etc.) rather than figures. They're cards in the dashboard (with a "pipeline" badge) but don't require an image. They can still emit `output.png` if useful.

**Downstream analyses read upstream artifacts** via `_lib/pipeline.py`:

```python
from _lib.pipeline import load_pickle, load_json

def run(output_dir):
    df = load_pickle("apply_exclusions", "trials.pkl")
    excluded = load_json("compute_exclusions", "exclusions.json")
```

The helper resolves the current collection's output root from runtime context (`labdash._context.CURRENT_OUTPUT_ROOT`). No hard-coded paths in analysis scripts.

## 4.6 Shared plot functions (`_lib/plots/`)

When the same plot applies to multiple collections (e.g., running identical analyses on real human data vs. agent-generated sim data), factor the logic into a pure function in `_lib/plots/`:

```python
# _lib/plots/sde_accuracy.py
def make(df, output_dir, *, figsize=(14, 5), title="SDE", ylim=None, ...) -> dict:
    ...  # plot logic; returns stats dict
```

Each collection's leaf `analysis.py` becomes a ~15-line wrapper setting its own aesthetic constants and declaring its own `dependencies:`:

```python
from _lib.pipeline import load_pickle
from _lib.plots.sde_accuracy import make
from _lib.style import apply_style

FIGSIZE = (14, 5); TITLE = "Pilot 1 — SDE"; YLIM = (0, 1.05)
UPSTREAM = "apply_exclusions"   # per-collection; different collections may depend on different pipelines

def run(output_dir):
    apply_style()
    df = load_pickle(UPSTREAM, "trials.pkl")
    return make(df, output_dir, figsize=FIGSIZE, title=TITLE, ylim=YLIM)
```

Agent rule: if a knob isn't exposed in the shared `make()`, add it as a kwarg (with the current behavior as default) and pass it in from the wrapper. Don't fork the shared function.

## 5. CLI Commands

```bash
# Build (force-rebuild everything in topo order)
labdash build -c path/to/collection

# Build only stale (skip fresh outputs; stale chains still run in order)
labdash build -c path/to/collection --only-stale

# Build one slug plus any transitively-stale upstream (behavior change from pre-DAG)
labdash build -c path/to/collection my_slug

# Serve (live development server with in-browser editing)
labdash serve -c path/to/collection               # localhost:8800 by default
labdash serve -c path/to/other_collection --port 8801   # parallel viewers

# Export (publication-ready output)
labdash export figures -c path/to/collection      # SVG by default
labdash export figures --format pdf
labdash export code -c path/to/collection         # self-contained code dir

# Scaffold
labdash init /path/to/project                     # new single-collection project
labdash new-wrapper <slug> -c path/to/collection --plot _lib.plots.<module> \
    [--upstream <pipeline_slug>] [--title ...] [--group ...]
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

For a leaf that wraps a shared `_lib/plots/<module>` function, prefer the scaffolder — it lays down the wrapper, meta.yaml, and notes.md with the right imports and dependencies:

```bash
labdash new-wrapper my_slug \
    -c path/to/collection \
    --plot _lib.plots.my_plot \
    --upstream build_trials_df \
    --title "My Analysis" \
    --group "Some Group"
```

For a bespoke leaf (or a new pipeline node):

1. **Create the directory** under the collection: `mkdir path/to/collection/my_slug`.
2. **Write `meta.yaml`** with title, group, description, methodology, tags, status, and `dependencies:` listing any upstream slugs.
3. **Write `analysis.py`** following the contract. Import from `_lib/`. Aesthetic variables at top. Load upstream artifacts via `_lib.pipeline.load_pickle()` / `load_json()`.
4. **Create `notes.md`**:
   ```markdown
   <!-- Your notes here. This file is never edited by AI agents. -->
   ```
5. **Run the script** to verify: `python path/to/collection/my_slug/analysis.py` (works standalone; data_loading walks up from cwd to find the collection.yaml).
6. **Read the output image** to verify it looks right.
7. **Build the dashboard** to see it in context: `labdash build -c path/to/collection my_slug` (auto-includes transitively-stale upstream).

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

### Import rules in `_lib/` (mandatory)

LabDash's transitive staleness detector walks `_lib/` imports statically. For this to be correct, files in `_lib/` and wrappers that import from `_lib/` must use:

- **Explicit imports only.** `from _lib.X import name1, name2` — never `from _lib.X import *`.
- **Top-level absolute imports.** No `importlib.import_module(...)`, no `__import__(...)`, no conditional imports of `_lib/` modules inside functions or `try/except` blocks.

Violations are caught at build time (`labdash build` / `labdash serve` / the in-browser shared-code editor) and abort with a `LibImportError` pointing at the file and line. The mandate exists so that editing any `_lib/` file — including transitively-imported leaves like `_lib/preprocessing.py` — reliably marks every dependent wrapper as stale.

### data_loading.py
- Contains functions like `load_all_participants()`, `build_trial_dataframe()`
- Returns DataFrames with a `participant_id` column for multi-participant analysis
- Handles JSON parsing, type conversions, filtering
- **Should use module-level caching** (`_cache = {}`) so data is loaded from disk once per process. Return copies (`df.copy()`, `copy.deepcopy()`) to prevent cross-analysis mutation. Cache is destroyed when "Reload Data" clears `_lib` from `sys.modules`.

### preprocessing.py
- Contains shared transforms used by multiple analyses
- Functions here are used by multiple analyses
- Keep analysis-specific logic in the analysis scripts themselves

### style.py
- Must include `matplotlib.use("agg")` before importing `pyplot` to avoid `RuntimeError` in server threads
- `apply_style()` — call at the start of every analysis
- Color palettes, font sizes, and figure defaults
- `DEFAULT_DPI`, `DEFAULT_FIGSIZE` — shared defaults

## 9. Handling Dependencies

`dependencies:` in `meta.yaml` forms a DAG. The runner topo-sorts execution, and **staleness propagates transitively** — touching any upstream source marks all downstream analyses as stale in the viewer.

### Passing scalar stats (simple case)

If B needs summary stats from A:

1. A returns a stats dict from `run()` (auto-saved as `stats.json`).
2. B's meta.yaml: `dependencies: [a_slug]`.
3. B's `run()` reads A's stats via the pipeline helper:
   ```python
   from _lib.pipeline import load_json
   a_stats = load_json("a_slug", "stats.json")
   ```

### Passing DataFrames / arbitrary artifacts (pipeline case)

For derived DataFrames, exclusion lists, or anything bigger than scalars, make A a pipeline node (`output_format: pipeline`) and have it write its artifact:

```python
# A/analysis.py (pipeline node)
def run(output_dir):
    df = ...              # compute / transform
    df.to_pickle(output_dir / "trials.pkl")
    return {"n_rows": len(df)}
```

B's `run()` loads it with the helper:

```python
from _lib.pipeline import load_pickle

def run(output_dir):
    df = load_pickle("a_slug", "trials.pkl")
    ...
```

### Hard "Run = run stale chain"

`labdash build <slug>` and the viewer's per-card **Run** button always run any transitively-stale upstream first, in topo order, then the target. No warnings — a Run always means run. Use `--only-stale` (CLI) or the **Run All Stale** button (viewer) to skip fresh work across the whole DAG.

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

The registry is the **source of truth for group ordering** and within-group analysis ordering. It is synced (not rebuilt) on every `labdash build` and metadata edit — existing order is never changed by sync, only new analyses are added and deleted ones removed.

```yaml
agent_notes: |
  Groups ordered by data pipeline stage: Pipeline → Design → ...
  Within each group, order by conceptual flow (overview → detail).

groups:
  - name: Pipeline
    analyses:
      - build_trials_df
      - apply_exclusions
  - name: Performance
    analyses:
      - accuracy_by_phase
      - rt_by_distance_combined

tags:
  - accuracy
  - arbitrage
  - icl
  - pipeline
  - rt
```

**Rules:**
- Read `registry.yaml` before assigning groups and tags to new analyses.
- Prefer existing groups and tags over creating new ones.
- The `agent_notes` top-level string field is for high-level organizational principles; survives YAML round-trips.
- When creating a new analysis, set `group:` in meta.yaml — on next build, sync appends it to the end of that group in the registry.
- You may edit `registry.yaml` directly to change ordering or group membership; registry wins for existing analyses.

## 14. Staleness Detection

`labdash build` computes staleness **transitively**:

- An analysis is self-stale if its `analysis.py` OR any `_lib/` file in its **transitive** import closure is newer than its oldest output file (or if it has no outputs). The closure is computed by static AST parse of the wrapper and every `_lib/*.py` it reaches; see "Import rules in `_lib/`" in §8 for the static-import mandate that makes this reliable.
- An analysis is dep-stale if any upstream dependency's newest output is newer than this one's oldest output, or if any upstream is itself stale.

Stale analyses are flagged with a red "stale" badge in the viewer. The **Run All Stale** button reruns only them, in topo order. Clicking **Run** on a single card runs any transitively-stale upstream before the target.

**Implication for shared-plot edits.** Editing `_lib/plots/X.py` — or any `_lib/` leaf that `X.py` itself imports (e.g. `_lib/preprocessing.py`) — automatically marks every wrapper that transitively imports it as stale. The sidebar editor's Save response lists the affected slugs; the viewer adds stale badges to those cards without a reload.

## 15. Live mode (file watcher + SSE)

When `labdash serve` is running, the server watches the collection's
`meta.yaml`, `registry.yaml`, every `analysis.py` wrapper, and every
`.py` under `_lib/` (whether the `_lib/` lives inside the collection
or as a sibling directory). Edits made directly on disk — by you, by
an AI agent, or by a build step — propagate to the open viewer via
Server-Sent Events on `GET /events`. There is no polling and no need
to refresh the page.

The viewer patches itself surgically for these changes:
- `_lib/*.py` edit → stale badges update; the in-card highlighted
  shared-code panes refresh; if a Monaco editor is currently open on
  that file the buffer is replaced **only if it is clean** — see the
  conflict rule below.
- `<slug>/analysis.py` edit → stale badge for that slug; the inline
  highlighted code refreshes; same Monaco-clean-only buffer rule for
  the wrapper editor.
- `<slug>/meta.yaml` edit to `title`, `description`, `methodology`,
  `status`, `tags`, or `figure_id` → the corresponding card field
  updates in place.
- `registry.yaml` edit that just reorders slugs within a group → the
  cards reorder in the DOM.
- `registry.yaml` edit that moves a slug between existing groups →
  the card moves in the DOM.

Some changes still trigger a full page reload (`location.reload()`),
because the DOM doesn't carry a structural template for them:
- a new group is added or an existing group is removed/renamed in
  `registry.yaml`
- a slug is added to or removed from the collection
- `meta.dependencies` or `meta.output_format` changes (affects
  pipeline lineage / output rendering)
- `collection.yaml` changes
- a wrapper or `meta.yaml` is deleted

**Monaco dirty-buffer rule.** If you have a `_lib/` or wrapper file
open in Monaco with unsaved edits and the same file changes on disk,
the buffer is **not** overwritten. A toast warns you and your local
edits are preserved; Save will overwrite the disk version, or close
the editor without saving to load the disk version.

**`_lib` import errors during live mode.** Saving a `_lib/` file (on
disk or via the in-browser editor) that introduces a star import, a
dynamic import, or a syntax error raises `LibImportError`. In live
mode this surfaces as a persistent banner at the top of the viewer
listing the file, line, and a fix hint. Stale-set updates pause until
the error is cleared; subsequent successful saves clear the banner.

**`meta.group` vs registry.** Editing `meta.group` for an *existing*
analysis on disk still does nothing — the registry remains the source
of truth for group membership (see §13). The server logs a warning;
the viewer ignores the change. To move an existing card between
groups, edit `registry.yaml` directly or use the in-app dropdown /
drag-and-drop.

**No auto-run.** Live mode does **not** automatically re-execute
stale analyses. To rerun, click Run on a card, Run All Stale, or
invoke `labdash build --only-stale` from a terminal. Auto-run is a
separate (future) feature.

## 16. Slice-aware manual ordering (per-card buttons + bulk select)

Every card carries four small ordering buttons in its header
(`⤒ ↑ ↓ ⤓`), muted at idle and brightening on card hover. They move
the card within its **slice** — the visible subset of its group under
the current filters (group filter + status filter + tag filter +
search). The slice is computed live: filter by a tag and `⤒` sends a
card to the top of *what's visible*, not the absolute top of the group.

* `⤒` — send to top of slice
* `↑` — move up one slot in the slice
* `↓` — move down one slot
* `⤓` — send to bottom of slice

Buttons that would be no-ops are greyed out (`disabled`). Cards
at the top of their slice have `⤒` and `↑` disabled; cards at the
bottom have `⤓` and `↓` disabled. Single-card slices have all four
disabled.

**These buttons stay within-group.** To move a card to a different
group, use drag-and-drop or edit `registry.yaml` directly — both
already-supported affordances. The buttons never cross group
boundaries.

**Bulk selection.** Shift-click a card to range-select from the
current anchor; Cmd/Ctrl-click toggles a single card in the
selection. Selected cards show a strong blue ring; a count chip in
the top-right corner shows the active count and a Clear button.
Clicking outside any card (in the main content area) clears the
selection. Click any move button on a selected card, and the action
applies to every selected card — each within its own group's slice,
preserving their relative DOM order for `⤒` / `⤓`.

**Persistence.** Like drag-and-drop, each move PUTs to
`/api/registry/order`; the file watcher echoes back a `card_order`
SSE event, which the originating client absorbs idempotently (no
visible re-shuffle). Other open viewers see the change live.

**Algorithm parity.** The slice math is duplicated in two places:
`labdash/_slice_order.py` (Python, exercised by `tests/test_slice_ordering.py`)
and the `computeNewOrder` / `resolveRange` block inside
`labdash/templates/index.html`. Change one, change the other.
