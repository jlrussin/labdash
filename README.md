# LabDash: AI-Agent-Friendly Analysis Dashboard

A modular analysis and visualization dashboard for scientific research, designed to replace Jupyter notebooks with plain Python scripts that AI coding agents can read, write, and verify.

---

## Table of Contents

- [What and Why](#what-and-why)
- [Status](#status)
- [Quick Start](#quick-start)
- [The analysis.py Contract](#the-analysispy-contract)
- [meta.yaml Format](#metayaml-format)
- [Aesthetic Variables Convention](#aesthetic-variables-convention)
- [Collections](#collections)
- [CLI Commands](#cli-commands)
- [Viewer Features](#viewer-features)
- [Shared Code (\_lib/)](#shared-code-_lib)
- [Agent Interaction Model](#agent-interaction-model)
- [Agent Notes and Change Log](#agent-notes-and-change-log)
- [Data Source Filtering](#data-source-filtering)
- [Publication Pipeline](#publication-pipeline)
- [Safety](#safety)
- [Contributing](#contributing)
- [License](#license)

---

## What and Why

Jupyter notebooks are hostile to AI coding agents:

- **Hidden cell state.** Execution order is implicit. A notebook can appear to work while harboring stale variables from cells run out of order.
- **JSON format.** Notebooks are stored as JSON with embedded outputs, making diffs unreadable and merge conflicts frequent.
- **Not runnable in isolation.** You cannot execute a notebook from the command line and trust the result without re-running every cell from scratch.

LabDash replaces notebooks with **plain `.py` files** that follow a standard contract. Each analysis is:

- **Standalone** -- one directory, one `analysis.py`, one `meta.yaml`.
- **Runnable** -- `python analysis.py` produces output in the same directory.
- **Verifiable** -- an agent (or a human) can run the script and confirm the output exists.
- **Diffable** -- plain Python, plain YAML, standard git diffs.

The dashboard viewer assembles all analyses into a browsable HTML interface with filtering, search, code viewing, and (in live mode) in-browser editing and execution.

---

## Status

> **LabDash is experimental, a work in progress, and not well-tested.** The API may change without notice. Use at your own risk.

---

## Quick Start

```bash
pip install -e .
labdash init my-project
cd my-project
labdash build
open _output/index.html
```

This creates a project with an example analysis, builds it, and opens the static dashboard in your browser. From there you can add your own analyses by creating new directories under `analyses/`.

---

## The analysis.py Contract

Every analysis lives in its own directory and follows this structure:

```
analyses/
  my_analysis/
    analysis.py    # the script (required)
    meta.yaml      # metadata (required)
    notes.md       # human notes (optional, never edited by agents)
    agent_notes.md # agent notes (optional, written by agents)
```

Here is the full `analysis.py` template:

```python
"""Short description of this analysis."""
import sys
from pathlib import Path

# ── _lib finder ─────────────────────────────────────────
# Makes the shared _lib/ package importable when running
# this script directly (python analysis.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
from _lib.data_loading import load_data
from _lib.style import apply_style, COLORS

# ── Aesthetic variables ─────────────────────────────────
FIGSIZE = (8, 5)
TITLE = "My Analysis Title"
XLABEL = "X Axis Label"
YLABEL = "Y Axis Label"
BAR_COLOR = COLORS["primary"]
# ────────────────────────────────────────────────────────


def run(output_dir: Path) -> dict:
    """Main entry point. Called by labdash build.

    Args:
        output_dir: Directory to write output files into.

    Returns:
        A dict of summary statistics (saved as stats.json).
    """
    apply_style()

    # ── Load and process data ──
    df = load_data()
    # ... your analysis logic here ...

    # ── Create figure ──
    fig, ax = plt.subplots(figsize=FIGSIZE)
    # ... your plotting code here ...
    ax.set_title(TITLE)
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(YLABEL)

    # ── Save ──
    fig.savefig(output_dir / "output.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    return {"n": len(df)}


if __name__ == "__main__":
    run(Path(__file__).parent)
```

### What each part does

| Section | Purpose |
|---------|---------|
| **`_lib` finder** | `sys.path.insert(0, ...)` ensures `from _lib.data_loading import load_data` works whether the script is run standalone (`python analysis.py`) or by `labdash build`. Always include this. |
| **Aesthetic variables block** | All visual parameters (sizes, titles, colors, limits) are declared as module-level constants at the top of the file. See [Aesthetic Variables Convention](#aesthetic-variables-convention). |
| **`run(output_dir)`** | The single entry point. Receives the output directory, writes files there (`output.png`, `output.svg`, `output.html` for tables), and returns a dict of summary statistics. |
| **`__main__` block** | Calls `run(Path(__file__).parent)` so the script works when run directly. Output goes to the analysis directory itself. |

---

## meta.yaml Format

Each analysis directory must contain a `meta.yaml` file:

```yaml
title: "Accuracy by Condition"         # Display title in the dashboard
group: "Behavioral"                    # Group heading for organization
order: 1                               # Sort order within group (lower = first)
description: >                         # Brief description shown on the card
  Compares mean accuracy across
  experimental conditions.
methodology: >                         # Expandable section with analysis details
  Two-sample t-test with Welch's
  correction. Error bars show 95% CI.
tags: [accuracy, between-subjects]     # Filterable tags
status: draft                          # draft | active | publication | disabled
output_format: png                     # png | svg | table
figure_id: "fig1a"                     # Identifier for publication export
caption: >                             # Figure caption for publication
  Mean accuracy by condition. Error
  bars indicate 95% confidence intervals.
dependencies: []                       # List of slugs that must run first
```

**Field reference:**

| Field | Required | Description |
|-------|----------|-------------|
| `title` | yes | Human-readable title displayed on the dashboard card. |
| `group` | yes | Groups analyses under a shared heading. Used for sidebar navigation. |
| `order` | no | Integer controlling sort order within a group. Default: 999. |
| `description` | no | Short summary shown directly on the card. |
| `methodology` | no | Longer explanation in a collapsible section. |
| `tags` | no | List of strings for filtering. |
| `status` | no | One of `draft`, `active`, `publication`, or `disabled`. Disabled analyses are skipped entirely. Default: `draft`. |
| `output_format` | no | Expected output type: `png`, `svg`, or `table`. Default: `png`. |
| `figure_id` | no | Used as the filename when exporting figures for publication. |
| `caption` | no | Figure caption included in the export manifest. |
| `dependencies` | no | List of analysis slugs that must run before this one. |

---

## Aesthetic Variables Convention

This is the key innovation for human iteration on AI-generated analyses.

All visual parameters are declared as **module-level constants** in a clearly marked block at the top of `analysis.py`, between the imports and the `run()` function:

```python
# ── Aesthetic variables ─────────────────────────────────
FIGSIZE = (10, 6)
TITLE = "Symbolic Distance Effect by Condition"
XLABEL = "Ordinal Distance"
YLABEL = "Proportion Correct"
COLORS = {"skewed": "#e74c3c", "uniform": "#3498db"}
XLIM = (1, 6)
YLIM = (0.4, 1.0)

# Layout spacing (for multi-panel figures)
SUPTITLE_Y = 0.98
TOP_MARGIN = 0.92
HSPACE = 0.3
WSPACE = 0.2

# Multi-panel options
SHARE_Y_AXIS = True          # Useful for side-by-side RT comparisons
# ────────────────────────────────────────────────────────
```

**Why this matters:**

- A human can scan the top of the file and tweak `TITLE`, `YLIM`, or `FIGSIZE` without reading the analysis logic.
- An AI agent can modify aesthetics without risking changes to the statistical computation below.
- In live mode, the viewer provides a Monaco code editor -- the aesthetic block is the first thing you see.

**What to include** (as applicable):

- `FIGSIZE` -- figure dimensions in inches.
- `TITLE`, `XLABEL`, `YLABEL` -- text labels.
- Colors -- palette dicts or individual color constants.
- `XLIM`, `YLIM` -- axis limits when the defaults are not appropriate.
- Layout spacing -- `SUPTITLE_Y`, `TOP_MARGIN`, `HSPACE`, `WSPACE` for `plt.subplots_adjust()`.
- `SHARE_Y_AXIS` -- boolean for multi-panel figures where panels should share a y-axis (common for side-by-side RT comparisons across conditions).

---

## Collections

A project can organize analyses into multiple **collections** -- separate sets of analyses that share the same `_lib/` code. This is useful when a project has distinct experiment phases or analysis themes.

```
my-project/
  labdash.yaml              # points to active collection
  data/
  analyses/
    _lib/                   # shared across all collections
      data_loading.py
      style.py
    pilot1/                 # one collection
      accuracy_by_condition/
      rt_by_distance/
    pilot2/                 # another collection
      accuracy_by_condition/
      learning_curves/
```

In `labdash.yaml`, set `analyses_dir` to the active collection:

```yaml
analyses_dir: "analyses/pilot1"    # switch to "analyses/pilot2" to view that set
```

The `_lib/` directory is resolved by walking up from the collection directory, so shared code is automatically available to all collections.

---

## CLI Commands

### `labdash build [slugs...]`

Run analyses and generate a static HTML dashboard.

```bash
labdash build                          # run all analyses
labdash build accuracy_by_condition    # run only this one
labdash build sde_plot rt_histogram    # run specific analyses
```

Output goes to the `output_dir` specified in `labdash.yaml` (default: `_output/`). The generated `_output/index.html` is a self-contained file you can open directly in a browser.

### `labdash serve [--port PORT]`

Start a live development server with in-browser editing and execution.

```bash
labdash serve                  # default port from config (8800)
labdash serve --port 9000      # custom port
```

Requires the `serve` extras: `pip install labdash[serve]`.

The live server adds:
- A Monaco code editor (replacing the static Pygments view).
- Edit / Save / Run / Save & Run buttons on each analysis card.
- Editable group, tags, and status fields directly in the viewer.
- All edits are written back to the source files on disk.

### `labdash export figures`

Export publication-ready figures.

```bash
labdash export figures                         # default: SVG, publication status only
labdash export figures --format pdf --dpi 600  # PDF at 600 DPI
labdash export figures --status active         # export active analyses instead
labdash export figures --output ./pub_figs     # custom output directory
```

Exports to `figures/` by default, with a `figure_manifest.yaml` mapping `figure_id` to filenames and captions.

### `labdash export code`

Export a self-contained code directory for sharing or archiving.

```bash
labdash export code                    # publication status only
labdash export code --status active    # export active analyses
labdash export code --output ./code    # custom output directory
```

Produces a directory with `_lib/`, each analysis, a `run_all.py` script, and a `requirements.txt`.

### `labdash init [path]`

Scaffold a new LabDash project.

```bash
labdash init my-project    # create in ./my-project
labdash init               # create in current directory
```

Creates: `labdash.yaml`, `analyses/_lib/` (with `data_loading.py`, `style.py`, `preprocessing.py`), and an example analysis.

---

## Viewer Features

### Sidebar

- **Search box** -- full-text search across all card content. Keyboard shortcut: `/`.
- **Group navigation** -- click a group name to filter to that group. "All" shows everything.
- **Tag filters** -- toggle tags on/off to filter analyses. Multiple tags use OR logic.
- **Status filters** -- filter by `draft`, `active`, `publication`, or `stale` (output older than source).
- **Shared code panel** -- click any `_lib/` file in the sidebar to open it in a slide-out panel. Press `Escape` to close.

### Card features

- **Collapsible** -- click the card header to collapse/expand. State persists across page loads (localStorage).
- **Draggable** -- drag cards to reorder them within the dashboard (live mode).
- **Status badge** -- color-coded (`draft` = yellow, `active` = blue, `publication` = green). Clickable in live mode to cycle through statuses.
- **Stale badge** -- red badge appears when `analysis.py` is newer than its output.
- **Figure ID badge** -- shown when `figure_id` is set in meta.yaml.
- **Expandable sections** -- Code, Methodology, Notes, Agent Notes, and Stats are all collapsible sections within each card.
- **Image actions** -- hover over output images to reveal copy/download buttons.

### Code view

- **Static mode** (`labdash build`): Pygments syntax highlighting with a copy button.
- **Live mode** (`labdash serve`): Monaco editor with Python language support, dark theme. Starts read-only; click "Edit" to enable editing, then "Save", "Run", or "Save & Run".

### Notes

- **notes.md** -- human notes, shown in a collapsible section. Editable in live mode. Never written by AI agents.
- **agent_notes.md** -- agent notes, shown in a separate collapsible section with distinct styling (monospace, muted color). Written by AI agents to explain their reasoning.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `j` | Move focus to next card |
| `k` | Move focus to previous card |
| `c` | Toggle collapse on focused card |
| `C` (Shift+C) | Collapse/expand all cards |
| `e` | Toggle code section on focused card |
| `m` | Toggle methodology section on focused card |
| `s` | Toggle stats section on focused card |
| `n` | Toggle notes section on focused card |
| `/` | Focus the search box |
| `Escape` | Close all expanded sections on focused card, or blur input |
| `?` | Show keyboard shortcut hint (auto-hides after 3 seconds) |

---

## Shared Code (\_lib/)

The `_lib/` directory holds code shared across all analyses in a collection:

```
_lib/
  __init__.py           # empty, makes _lib a package
  data_loading.py       # single source of truth for data loading
  style.py              # centralized colors, fonts, figure defaults
  preprocessing.py      # shared data transformations
```

**How agents should use it:**

- **`data_loading.py`** -- all analyses import `load_data()` from here. When the data format changes, update this one file.
- **`style.py`** -- defines `apply_style()`, color palettes, font sizes, and figure defaults. Call `apply_style()` at the top of every `run()` function.
- **`preprocessing.py`** -- put transformations here when two or more analyses need the same filtering, grouping, or derived columns.
- Agents should never duplicate data-loading or preprocessing logic in individual analysis scripts. If a transformation is used in more than one analysis, move it to `_lib/`.

---

## Agent Interaction Model

LabDash is designed so that an AI coding agent can:

1. **Create an analysis** -- write `analysis.py` and `meta.yaml` in a new directory under the analyses folder.
2. **Edit an analysis** -- modify the `.py` file directly. Plain Python, no notebook cell boundaries to navigate.
3. **Run an analysis** -- execute `python analysis.py` or `labdash build slug_name` and check exit code.
4. **Verify output** -- confirm that `output.png` (or `.svg`, or `output.html`) was produced.
5. **Read the change log** -- inspect `change_log.md` to learn what the user changed in the viewer (style preferences, label tweaks, etc.).
6. **Write agent notes** -- document reasoning and decisions in `agent_notes.md`.

**What makes this agent-friendly:**

- **Plain `.py` files** -- no JSON cell format, no hidden state, no execution order ambiguity.
- **One file per analysis** -- agents work on a single file at a time without side effects.
- **Runnable in isolation** -- `python analysis.py` always works. No kernel state to manage.
- **Verifiable output** -- the agent can check whether the expected output file exists after running.
- **Aesthetic/logic separation** -- visual parameters at the top, computation below. An agent can adjust one without touching the other.

---

## Agent Notes and Change Log

### agent_notes.md

Each analysis directory can contain an `agent_notes.md` file. This is written by the AI agent to explain what the analysis does, why certain decisions were made, and what to watch out for. It is displayed in the viewer in a distinct monospace style.

A collection-level `agent_notes.md` can also live in the analyses directory root for project-wide notes.

### change_log.md

When a user edits code, notes, or metadata through the live viewer (`labdash serve`), LabDash automatically appends a timestamped entry to `change_log.md` in the analyses directory. Each entry includes a unified diff of what changed.

Agents should read this file to learn user preferences -- for example, if the user repeatedly adjusts `YLIM` or changes color choices, the agent can adopt those preferences in future analyses.

### registry.yaml

Auto-generated by `labdash build`. Contains a snapshot of all current groups and which analyses belong to each group, plus a list of all tags in use. Agents can read this to understand the project structure without scanning every `meta.yaml`.

---

## Data Source Filtering

The `data_filter` field in `labdash.yaml` controls which data subset the dashboard is built from:

```yaml
data_filter: "test"    # all | prod | test
```

- **`all`** -- use all available data.
- **`prod`** -- production data only (e.g., real participants).
- **`test`** -- test/pilot data only.

The current filter is displayed as a colored badge in the dashboard sidebar (`test` = yellow, `prod` = green, `all` = blue). This makes it immediately visible which data you are looking at, preventing accidental mixing of test and production data during analysis.

Your `_lib/data_loading.py` should read this setting and filter accordingly.

---

## Publication Pipeline

LabDash supports a workflow from exploratory draft to publication-ready figure:

1. **Draft** -- initial exploration. Create the analysis, iterate on the logic.
2. **Active** -- analysis is correct and maintained. Update it as new data arrives.
3. **Publication** -- final version for a paper. Set `figure_id` and `caption` in `meta.yaml`.

When ready to export:

```bash
# Export all publication-status figures as SVG
labdash export figures

# Export self-contained code for a methods supplement
labdash export code
```

`labdash export figures` re-runs each publication analysis and copies output to a flat `figures/` directory, named by `figure_id`. A `figure_manifest.yaml` maps figure IDs to filenames and captions.

`labdash export code` gathers `_lib/` and all publication analyses into a standalone directory with a `run_all.py` and `requirements.txt`, suitable for sharing as supplementary material.

---

## Safety

> **AI agents can make mistakes in data analysis.** Always verify results yourself. Do not give agents access to private or sensitive data. The scientist is responsible for the science.

---

## Contributing

Contributions are welcome. LabDash is in early development -- if you find bugs or have ideas, please open an issue or pull request.

---

## License

MIT
