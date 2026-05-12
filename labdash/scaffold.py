"""Scaffold a new labdash analysis set and new wrappers/pipelines."""

import textwrap
from pathlib import Path


def init_project(target: Path, *, language: str = "python"):
    """Create a single-collection LabDash project in `target`.

    `language` is `"python"` (default) or `"r"`. For Python collections,
    `_lib/` and example analyses use `.py`; for R, `.R`. The
    `collection.yaml`'s `language:` field records the choice, which the
    runtime consults to pick the right adapter.
    """
    language = language.lower()
    if language not in ("python", "r"):
        raise SystemExit(f"labdash init: unknown language: {language!r}. "
                         "Use 'python' or 'r'.")
    if language == "r":
        _init_r_project(target)
    else:
        _init_python_project(target)


def _init_python_project(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    lib_dir = target / "_lib"
    plots_dir = lib_dir / "plots"
    pipeline_ex = target / "example_pipeline"
    analysis_ex = target / "example_analysis"

    for d in [lib_dir, plots_dir, pipeline_ex, analysis_ex]:
        d.mkdir(parents=True, exist_ok=True)

    _write_if_missing(target / "collection.yaml", _COLLECTION_YAML)
    _write_if_missing(lib_dir / "__init__.py", "")
    _write_if_missing(lib_dir / "data_loading.py", _DATA_LOADING_PY)
    _write_if_missing(lib_dir / "style.py", _STYLE_PY)
    _write_if_missing(lib_dir / "preprocessing.py", _PREPROCESSING_PY)
    _write_if_missing(lib_dir / "pipeline.py", _PIPELINE_PY)
    _write_if_missing(plots_dir / "__init__.py", "")
    _write_if_missing(plots_dir / "README.md", _PLOTS_README)
    _write_if_missing(plots_dir / "example.py", _PLOTS_EXAMPLE)

    _write_if_missing(pipeline_ex / "meta.yaml", _EXAMPLE_PIPELINE_META)
    _write_if_missing(pipeline_ex / "analysis.py", _EXAMPLE_PIPELINE_PY)
    _write_if_missing(pipeline_ex / "notes.md", _NOTES_PLACEHOLDER)
    _write_if_missing(analysis_ex / "meta.yaml", _EXAMPLE_ANALYSIS_META)
    _write_if_missing(analysis_ex / "analysis.py", _EXAMPLE_ANALYSIS_PY)
    _write_if_missing(analysis_ex / "notes.md", _NOTES_PLACEHOLDER)

    _ensure_gitignore(target)

    print(f"\nLabDash collection initialized at {target}")
    print(f"  Edit collection.yaml to set data_dir and title")
    print(f"  Edit _lib/data_loading.py to match your data format")
    print(f"  Run: labdash build")


def _init_r_project(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    lib_dir = target / "_lib"
    plots_dir = lib_dir / "plots"
    analysis_ex = target / "example_analysis"

    for d in [lib_dir, plots_dir, analysis_ex]:
        d.mkdir(parents=True, exist_ok=True)

    _write_if_missing(target / "collection.yaml", _COLLECTION_YAML_R)
    _write_if_missing(lib_dir / "data_loading.R", _DATA_LOADING_R)
    _write_if_missing(lib_dir / "style.R", _STYLE_R)
    _write_if_missing(plots_dir / "README.md", _PLOTS_README_R)
    _write_if_missing(plots_dir / "example.R", _PLOTS_EXAMPLE_R)

    _write_if_missing(analysis_ex / "meta.yaml", _EXAMPLE_ANALYSIS_META_R)
    _write_if_missing(analysis_ex / "analysis.R", _EXAMPLE_ANALYSIS_R)
    _write_if_missing(analysis_ex / "notes.md", _NOTES_PLACEHOLDER)

    _ensure_gitignore(target)

    print(f"\nLabDash R collection initialized at {target}")
    print(f"  Edit collection.yaml to set data_dir and title")
    print(f"  Edit _lib/data_loading.R to load your data")
    print(f"  Required R packages: arrow, jsonlite, ggplot2 (and your stats packages)")
    print(f"  Run: labdash build")


def _ensure_gitignore(target: Path) -> None:
    gitignore = target / ".gitignore"
    ignore_line = "_output/"
    if gitignore.exists():
        content = gitignore.read_text()
        if ignore_line not in content:
            with open(gitignore, "a") as f:
                f.write(f"\n{ignore_line}\n")
    else:
        gitignore.write_text(f"{ignore_line}\n")


def new_wrapper(
    *,
    analyses_dir: Path,
    slug: str,
    plot_module: str,
    upstream: str | None = None,
    upstream_file: str = "trials.parquet",
    deps: list[str] | None = None,
    title: str | None = None,
    group: str = "Uncategorized",
):
    """Scaffold a leaf wrapper analysis calling a shared plots module.

    Language follows the collection (`collection.yaml: language:`).
    Python collections get `analysis.py` importing `from {plot_module} import make`;
    R collections get `analysis.R` sourcing the corresponding `_lib/plots/<file>.R`
    (where `plot_module` is interpreted as a path relative to `_lib/`,
    e.g. `plots/sde_accuracy` → `_lib/plots/sde_accuracy.R`).
    """
    from .languages import resolve_language_for, RLanguage

    target_dir = analyses_dir / slug
    if target_dir.exists():
        raise SystemExit(f"Wrapper already exists: {target_dir}")
    target_dir.mkdir(parents=True)

    language = resolve_language_for(analyses_dir)
    module_short = plot_module.rsplit(".", 1)[-1]
    title_final = title or slug.replace("_", " ").title()
    deps_final = deps or ([upstream] if upstream else [])

    if language is RLanguage:
        # `plot_module` for R is treated as a `_lib/`-relative dotted path
        # without an extension, e.g. `plots.example` → `plots/example.R`.
        rel = plot_module.replace(".", "/") + ".R"
        wrapper_code = _WRAPPER_TEMPLATE_R.format(
            title=title_final,
            plot_rel=rel,
            upstream_value=f'"{upstream}"' if upstream else "NULL",
            upstream_file=upstream_file,
        )
        (target_dir / "analysis.R").write_text(wrapper_code)
        methodology_ref = f"`_lib/{rel}`"
    else:
        wrapper_code = _WRAPPER_TEMPLATE.format(
            title=title_final,
            plot_module=plot_module,
            upstream_const=f'"{upstream}"' if upstream else "None",
            upstream_file=upstream_file,
        )
        (target_dir / "analysis.py").write_text(wrapper_code)
        methodology_ref = f"`_lib/plots/{module_short}.py`"

    meta_body = textwrap.dedent(
        f"""\
        title: "{title_final}"
        group: "{group}"
        description: >
          Wrapper around {methodology_ref} for this collection.
        methodology: >
          See {methodology_ref} for the shared plot logic.
        tags: []
        status: draft
        output_format: png
        dependencies: {deps_final}
        """
    )
    (target_dir / "meta.yaml").write_text(meta_body)
    (target_dir / "notes.md").write_text(_NOTES_PLACEHOLDER)

    print(f"Created {target_dir}")
    print(f"  {language.analysis_filename} imports from {plot_module}")
    if upstream:
        print(f"  upstream: {upstream} / {upstream_file}")


# ── Helpers ─────────────────────────────────────────────────────


def _write_if_missing(path: Path, content: str):
    if not path.exists():
        path.write_text(content)
        print(f"  Created {path}")


# ── Templates ───────────────────────────────────────────────────


_NOTES_PLACEHOLDER = "<!-- Your notes here. This file is never edited by AI agents. -->\n"


_COLLECTION_YAML = textwrap.dedent(
    """\
    # LabDash collection configuration
    title: "My Analyses"

    # Data source (consumed by _lib/data_loading.py via labdash runtime context).
    data_dir: "data"       # path (relative to project root) to your data directory
    data_filter: "all"     # all | prod | test (interpretation is up to data_loading.py)

    # Optional overrides:
    # output_dir: "_output/my_analyses"
    # port: 8800
    # publication_format: svg
    # publication_dpi: 300
    """
)


_DATA_LOADING_PY = textwrap.dedent(
    '''\
    """Load and prepare data for analysis.

    Reads `data_dir` / `data_filter` from the collection.yaml via the labdash
    runtime context (labdash._context.CURRENT_COLLECTION_CONFIG). Falls back
    to walking up from __file__ looking for collection.yaml when run standalone
    (python analysis.py).
    """

    import copy
    import json
    from pathlib import Path

    import pandas as pd

    try:
        from labdash import _context
    except ImportError:  # running without labdash installed
        _context = None

    import yaml

    _cache: dict = {}


    def _collection_config() -> dict:
        """Return the active collection's config dict."""
        if _context and _context.CURRENT_COLLECTION_CONFIG is not None:
            return _context.CURRENT_COLLECTION_CONFIG
        # Fallback: walk up from this file looking for a collection.yaml.
        for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
            cfg = d / "collection.yaml"
            if cfg.exists():
                return yaml.safe_load(cfg.read_text()) or {}
        return {}


    def _collection_dir() -> Path:
        """Directory containing the active collection.yaml."""
        if _context and _context.CURRENT_COLLECTION_DIR is not None:
            return _context.CURRENT_COLLECTION_DIR
        for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
            if (d / "collection.yaml").exists():
                return d
        return Path.cwd()


    def data_dir() -> Path:
        """Resolve `data_dir` from collection.yaml.

        An absolute path is used as-is. Otherwise the path is tried relative
        to the collection directory first, then relative to its parent
        (supporting multi-collection layouts where data lives at the project
        root alongside the collection subdirs).
        """
        cfg = _collection_config()
        d = cfg.get("data_dir")
        if not d:
            raise RuntimeError("No `data_dir` in collection.yaml")
        p = Path(d)
        if p.is_absolute():
            return p
        col = _collection_dir()
        candidate = (col / p).resolve()
        if candidate.exists():
            return candidate
        parent_candidate = (col.parent / p).resolve()
        if parent_candidate.exists():
            return parent_candidate
        # Default to the collection-relative path for helpful error messages
        return candidate


    def data_filter() -> str:
        return _collection_config().get("data_filter", "all")


    def load_data() -> pd.DataFrame:
        """Load all data into a single DataFrame. Edit to match your format."""
        cache_key = ("load_data", str(data_dir()), data_filter())
        if cache_key in _cache:
            return _cache[cache_key].copy()

        records = []
        dd = data_dir()
        if not dd.exists():
            raise FileNotFoundError(f"Data directory not found: {dd}")

        for f in sorted(dd.glob("*.json")):
            with open(f) as fh:
                doc = json.load(fh)
            if "trials" in doc:
                for trial in doc["trials"]:
                    trial["participant_id"] = doc.get("participant_id", f.stem)
                    records.append(trial)

        if not records:
            raise ValueError(f"No data found in {dd}")

        df = pd.DataFrame(records)
        _cache[cache_key] = df
        return df.copy()


    def clear_cache():
        _cache.clear()
    '''
)


_STYLE_PY = textwrap.dedent(
    '''\
    """Centralized aesthetic variables for all analyses.

    Edit this file to change colors, fonts, and figure defaults
    across all plots. All analysis scripts import from here.
    """

    import matplotlib
    matplotlib.use("agg")  # non-interactive backend, required for labdash serve
    import matplotlib.pyplot as plt
    import seaborn as sns


    COLORS = {
        "primary": "#3498db",
        "secondary": "#2ecc71",
        "accent": "#e74c3c",
        "neutral": "#95a5a6",
    }

    DEFAULT_DPI = 150
    PUBLICATION_DPI = 300
    DEFAULT_FIGSIZE = (8, 5)

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
    '''
)


_PREPROCESSING_PY = textwrap.dedent(
    '''\
    """Common data transformations shared across analyses.

    Functions here are used by multiple analysis scripts. Keep
    analysis-specific logic in individual analysis.py files.
    """

    import pandas as pd  # noqa: F401
    '''
)


_PIPELINE_PY = textwrap.dedent(
    '''\
    """Artifact helper for DAG pipelines.

    Lets a downstream analysis read an upstream transform's output artifact
    (a parquet DataFrame, a JSON list, anything else) without knowing where
    labdash puts its output directory.

    Usage inside analysis.py:

        from _lib.pipeline import load_parquet, upstream

        def run(output_dir):
            df = load_parquet("build_trials_df", "trials.parquet")
            # ... plot it ...

    Falls back to walking up from __file__ when run standalone (python analysis.py).
    """

    from __future__ import annotations

    import json
    from pathlib import Path

    try:
        from labdash import _context
    except ImportError:
        _context = None


    def _output_root() -> Path:
        """Collection-level output root (contains <slug>/ subdirs)."""
        if _context and _context.CURRENT_OUTPUT_ROOT is not None:
            return _context.CURRENT_OUTPUT_ROOT
        # Fallback: derive from the analysis script location.
        # Assumes running as `python <collection>/<slug>/analysis.py` which
        # writes directly into its own directory — treat that as the output root.
        import inspect
        frame = inspect.stack()[2]
        p = Path(frame.filename).resolve().parent.parent
        return p


    def upstream(slug: str, filename: str) -> Path:
        """Path to a file in an upstream slug's output directory."""
        return _output_root() / slug / filename


    def load_parquet(slug: str, filename: str = "trials.parquet"):
        """Load a parquet artifact (requires pyarrow or fastparquet)."""
        import pandas as pd
        return pd.read_parquet(upstream(slug, filename))


    def load_pickle(slug: str, filename: str = "trials.pkl"):
        """Load a pickle artifact (no extra dependencies)."""
        import pandas as pd
        return pd.read_pickle(upstream(slug, filename))


    def load_json(slug: str, filename: str) -> dict | list:
        with open(upstream(slug, filename)) as f:
            return json.load(f)
    '''
)


_PLOTS_README = textwrap.dedent(
    """\
    # Shared Plot Functions

    Pure plotting functions go here, one per file. Each module exposes a
    `make(df, output_dir, **kwargs) -> dict` function:

    - takes a DataFrame and an output directory;
    - accepts aesthetic parameters as keyword arguments (figsize, title,
      colors, ylim, etc.) with sensible defaults;
    - writes `output.png` (or `output.svg`) to `output_dir`;
    - returns a stats dict (auto-saved as stats.json by labdash).

    A collection's leaf analyses become thin wrappers (~15 lines) that load
    the input DataFrame via `_lib.pipeline.load_parquet(...)`, set collection-
    specific aesthetic constants at the top of the file, and call `make()`.

    See `_lib/plots/example.py` and `example_analysis/analysis.py` for a
    minimal working example.
    """
)


_PLOTS_EXAMPLE = textwrap.dedent(
    '''\
    """Example shared plot function."""
    from pathlib import Path

    import matplotlib.pyplot as plt
    import numpy as np


    def make(df, output_dir: Path, *,
             figsize=(8, 5),
             title="Example Plot",
             xlabel="Value",
             ylabel="Count",
             n_bins=30,
             color="#3498db") -> dict:
        fig, ax = plt.subplots(figsize=figsize)
        vals = df.iloc[:, 0].to_numpy() if len(df.columns) else np.array([])
        ax.hist(vals, bins=n_bins, color=color, edgecolor="white", alpha=0.8)
        ax.set_title(title); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
        fig.savefig(output_dir / "output.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return {"n": len(vals)}
    '''
)


_EXAMPLE_PIPELINE_META = textwrap.dedent(
    '''\
    title: "Example Pipeline Node"
    group: "Pipeline"
    description: >
      Loads raw data via _lib.data_loading.load_data and writes a parquet
      file that downstream leaf analyses consume.
    methodology: >
      No transformation — this is a minimal pipeline example. Real pipelines
      typically filter, merge, or compute derived columns here.
    tags: [pipeline]
    status: active
    output_format: pipeline
    dependencies: []
    '''
)


_EXAMPLE_PIPELINE_PY = textwrap.dedent(
    '''\
    """Example pipeline node: load data and persist as a DataFrame artifact.

    Uses pickle for portability (no pyarrow dependency). Real projects
    often prefer parquet — `df.to_parquet(output_dir / "trials.parquet")`
    and `load_parquet(...)` in downstream analyses — once pyarrow is
    installed.
    """
    import sys
    from pathlib import Path

    _d = Path(__file__).resolve().parent
    while not (_d / "_lib").is_dir():
        _d = _d.parent
    sys.path.insert(0, str(_d))

    from _lib.data_loading import load_data


    def run(output_dir: Path) -> dict:
        df = load_data()
        df.to_pickle(output_dir / "trials.pkl")
        return {"n_rows": int(len(df)), "n_cols": int(len(df.columns))}


    if __name__ == "__main__":
        run(Path(__file__).parent)
    '''
)


_EXAMPLE_ANALYSIS_META = textwrap.dedent(
    '''\
    title: "Example Analysis"
    group: "Examples"
    description: >
      Thin wrapper around _lib.plots.example.make, consuming the example
      pipeline node's parquet output.
    methodology: >
      See _lib/plots/example.py.
    tags: [example]
    status: draft
    output_format: png
    dependencies: [example_pipeline]
    '''
)


_EXAMPLE_ANALYSIS_PY = textwrap.dedent(
    '''\
    """Example leaf analysis — wraps _lib.plots.example."""
    import sys
    from pathlib import Path

    _d = Path(__file__).resolve().parent
    while not (_d / "_lib").is_dir():
        _d = _d.parent
    sys.path.insert(0, str(_d))

    from _lib.pipeline import load_pickle
    from _lib.plots.example import make
    from _lib.style import apply_style

    # ── Aesthetic variables ──────────────────────────────
    FIGSIZE = (8, 5)
    TITLE = "Example Analysis"
    XLABEL = "Value"
    YLABEL = "Count"
    N_BINS = 30
    # ─────────────────────────────────────────────────────


    def run(output_dir: Path) -> dict:
        apply_style()
        df = load_pickle("example_pipeline", "trials.pkl")
        return make(df, output_dir,
                    figsize=FIGSIZE, title=TITLE,
                    xlabel=XLABEL, ylabel=YLABEL, n_bins=N_BINS)


    if __name__ == "__main__":
        run(Path(__file__).parent)
    '''
)


_WRAPPER_TEMPLATE = '''\
"""{title}"""
import sys
from pathlib import Path

_d = Path(__file__).resolve().parent
while not (_d / "_lib").is_dir():
    _d = _d.parent
sys.path.insert(0, str(_d))

from {plot_module} import make
from _lib.pipeline import load_parquet
from _lib.style import apply_style

# ── Aesthetic variables ──────────────────────────────────
FIGSIZE = (8, 5)
TITLE = "{title}"
# ─────────────────────────────────────────────────────────

UPSTREAM = {upstream_const}
UPSTREAM_FILE = "{upstream_file}"


def run(output_dir: Path) -> dict:
    apply_style()
    df = load_parquet(UPSTREAM, UPSTREAM_FILE) if UPSTREAM else None
    return make(df, output_dir, figsize=FIGSIZE, title=TITLE)


if __name__ == "__main__":
    run(Path(__file__).parent)
'''


# ── R templates ─────────────────────────────────────────────────


_COLLECTION_YAML_R = textwrap.dedent(
    """\
    # LabDash R collection configuration.
    title: "My R Analyses"
    language: r

    # Data source. R's `_lib/data_loading.R` reads this however it likes —
    # commonly via `arrow::read_parquet(file.path(data_dir, "trials.parquet"))`.
    data_dir: "data"
    data_filter: "all"

    # Optional overrides:
    # output_dir: "_output/my_r"
    # port: 8800
    # publication_format: svg
    # publication_dpi: 300
    """
)


_DATA_LOADING_R = textwrap.dedent(
    '''\
    # Load and prepare data for analysis.
    #
    # Reads `data_dir` / `data_filter` from `collection.yaml` via the
    # labdash R runtime helpers (`lab_collection_config`, `lab_collection_dir`).
    # Falls back to walking up from the script's working directory when run
    # standalone via `Rscript analysis.R`.

    .data_dir <- function() {
      cfg <- lab_collection_config()
      d <- cfg$data_dir %||% "data"
      coll <- lab_collection_dir()
      if (is.character(d) && nzchar(d)) {
        if (startsWith(d, "/")) return(d)
        return(normalizePath(file.path(coll, d), mustWork = FALSE))
      }
      file.path(coll, "data")
    }

    `%||%` <- function(a, b) if (!is.null(a)) a else b

    load_trials <- function(filename = "trials.parquet") {
      load_parquet_path(file.path(.data_dir(), filename))
    }

    load_parquet_path <- function(path) {
      if (!requireNamespace("arrow", quietly = TRUE)) {
        stop("`arrow` package required for parquet I/O.", call. = FALSE)
      }
      arrow::read_parquet(path)
    }
    '''
)


_STYLE_R = textwrap.dedent(
    '''\
    # Shared plotting style. Apply at the start of every analysis with
    # `apply_style()`.

    LABDASH_COLORS <- list(
      blue   = "#3b82f6",
      orange = "#f59e0b",
      green  = "#10b981",
      red    = "#ef4444",
      purple = "#8b5cf6",
      grey   = "#6b7280"
    )

    apply_style <- function() {
      if (!requireNamespace("ggplot2", quietly = TRUE)) return(invisible())
      ggplot2::theme_set(ggplot2::theme_minimal(base_size = 12))
    }
    '''
)


_PLOTS_README_R = textwrap.dedent(
    """\
    # Shared Plot Functions (R)

    Pure plotting functions go here, one per file. Each module exposes a
    `make(df, output_dir, ...)` function:

    - takes a data.frame and an output directory;
    - accepts aesthetic parameters as named arguments;
    - writes `output.png` (or `output.svg`) to `output_dir`;
    - returns a stats list (serialised by labdash to `stats.json`).

    A collection's leaf analyses become thin wrappers that source the
    plot module via `lab_source("plots/<file>.R")`, set
    collection-specific aesthetic constants at the top, and call `make()`.

    See `_lib/plots/example.R` and `example_analysis/analysis.R` for a
    minimal working example.
    """
)


_PLOTS_EXAMPLE_R = textwrap.dedent(
    '''\
    # Example shared plot function (R).
    #
    # Sourced by leaf wrappers via `lab_source("plots/example.R")`.

    make_example_plot <- function(df, output_dir,
                                  title = "Example Plot",
                                  fill_color = "#3b82f6") {
      if (!requireNamespace("ggplot2", quietly = TRUE)) {
        stop("`ggplot2` required for plotting.", call. = FALSE)
      }
      p <- ggplot2::ggplot(df, ggplot2::aes(x = .data[[names(df)[1]]])) +
        ggplot2::geom_histogram(fill = fill_color, alpha = 0.8, bins = 30) +
        ggplot2::labs(title = title)
      ggplot2::ggsave(file.path(output_dir, "output.png"), plot = p,
                     width = 8, height = 5, dpi = 150)
      list(n = nrow(df))
    }
    '''
)


_EXAMPLE_ANALYSIS_META_R = textwrap.dedent(
    '''\
    title: "Example R Analysis"
    group: "Examples"
    description: >
      Minimal R analysis that prints "hello" and writes a tiny table.
    methodology: >
      Replace with the actual statistical / visualisation procedure.
    tags: [example]
    status: draft
    output_format: table
    dependencies: []
    '''
)


_EXAMPLE_ANALYSIS_R = textwrap.dedent(
    '''\
    # Example R analysis — minimal end-to-end wiring.
    #
    # The `run()` function is called by labdash with the slug's output
    # directory. Return a list to be serialised as `stats.json`.

    lab_source("style.R")

    # ── Aesthetic variables ──────────────────────────────────────────
    TITLE <- "Example R Analysis"
    # ─────────────────────────────────────────────────────────────────


    run <- function(output_dir) {
      apply_style()

      writeLines(
        sprintf("<h3>%s</h3><p>Replace this with your real analysis.</p>",
                TITLE),
        file.path(output_dir, "output.html")
      )

      list(
        title = TITLE,
        n = 0L
      )
    }
    '''
)


_WRAPPER_TEMPLATE_R = '''\
# {title}
#
# Wrapper around `_lib/{plot_rel}` for this collection.

lab_source("{plot_rel}")
lab_source("style.R")

# ── Aesthetic variables ──────────────────────────────
TITLE <- "{title}"
# ─────────────────────────────────────────────────────

UPSTREAM <- {upstream_value}
UPSTREAM_FILE <- "{upstream_file}"


run <- function(output_dir) {{
  apply_style()
  df <- if (!is.null(UPSTREAM)) load_parquet(UPSTREAM, UPSTREAM_FILE) else NULL
  make_example_plot(df, output_dir, title = TITLE)
}}
'''
