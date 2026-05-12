# LabDash R runtime helper.
#
# Sourced by `r_bootstrap/run_analysis.R` before the user's analysis
# script runs. Provides:
#
#   lab_source(rel)         — strict import for `_lib/`-relative paths.
#                             The canonical way for `analysis.R` and
#                             other `_lib/` files to pull in shared R
#                             code. The static parser in Python only
#                             recognises this form.
#
#   load_parquet(slug, filename = "trials.parquet")
#                           — read an upstream slug's parquet artifact.
#                             Resolves the path via LABDASH_OUTPUT_ROOT.
#
#   load_json(slug, filename)
#                           — read an upstream slug's JSON artifact as a
#                             list.
#
#   upstream(slug, filename)
#                           — absolute path to a file in an upstream
#                             slug's output directory.
#
#   lab_collection_dir()    — collection root.
#   lab_output_root()       — collection-level output root.
#   lab_output_dir()        — this slug's output directory. (Note the
#                             `lab_` prefix: avoids shadowing the
#                             `output_dir` argument that `run()` takes.)
#   lab_collection_config() — list parsed from collection.yaml.
#
# When called outside of a labdash run (interactive R session, ad-hoc
# `Rscript analysis.R`), the env vars are unset and these helpers fall
# back to walking up from the calling script's directory looking for
# `collection.yaml`. Mirrors Python's `_context` fallback behaviour.

# ── Env getters (with standalone fallbacks) ────────────────────────────

.labdash_env_or <- function(name, fallback_fn) {
  v <- Sys.getenv(name, unset = "")
  if (nzchar(v)) v else fallback_fn()
}

.labdash_walk_for_collection <- function() {
  # Walk up from the current working directory looking for collection.yaml.
  # Mirrors `_lib/data_loading.py`'s standalone-mode fallback.
  d <- normalizePath(getwd(), mustWork = FALSE)
  repeat {
    if (file.exists(file.path(d, "collection.yaml"))) return(d)
    parent <- dirname(d)
    if (identical(parent, d)) return(NA_character_)
    d <- parent
  }
}

lab_collection_dir <- function() {
  .labdash_env_or("LABDASH_COLLECTION_DIR", .labdash_walk_for_collection)
}

lab_output_root <- function() {
  .labdash_env_or("LABDASH_OUTPUT_ROOT", function() {
    file.path(lab_collection_dir(), "_output")
  })
}

lab_output_dir <- function() {
  v <- Sys.getenv("LABDASH_OUTPUT_DIR", unset = "")
  if (nzchar(v)) return(v)
  # Standalone: write into the script's own directory.
  normalizePath(getwd(), mustWork = FALSE)
}

lab_collection_config <- function() {
  path <- Sys.getenv("LABDASH_COLLECTION_CONFIG", unset = "")
  if (!nzchar(path)) path <- file.path(lab_collection_dir(), "collection.yaml")
  if (!file.exists(path)) return(list())
  if (!requireNamespace("yaml", quietly = TRUE)) {
    stop("LabDash: lab_collection_config() requires the `yaml` package. ",
         "Install via `install.packages(\"yaml\")`.", call. = FALSE)
  }
  yaml::read_yaml(path)
}

# ── Strict `_lib/` import ──────────────────────────────────────────────

lab_source <- function(rel) {
  if (!is.character(rel) || length(rel) != 1L || !nzchar(rel)) {
    stop("lab_source(): `rel` must be a single non-empty character string.",
         call. = FALSE)
  }
  if (grepl("\\.\\.", rel, fixed = FALSE)) {
    stop(sprintf("lab_source(\"%s\"): paths must not contain '..'.", rel),
         call. = FALSE)
  }
  lib <- Sys.getenv("LABDASH_LIB_DIR", unset = "")
  if (!nzchar(lib)) {
    # Standalone fallback: walk up from CWD to find `_lib/`.
    d <- normalizePath(getwd(), mustWork = FALSE)
    repeat {
      candidate <- file.path(d, "_lib")
      if (dir.exists(candidate)) { lib <- candidate; break }
      parent <- dirname(d)
      if (identical(parent, d)) break
      d <- parent
    }
  }
  if (!nzchar(lib)) {
    stop(sprintf(
      "lab_source(\"%s\"): could not locate `_lib/` (LABDASH_LIB_DIR not set and no `_lib/` found by walking up).",
      rel), call. = FALSE)
  }
  target <- file.path(lib, rel)
  if (!file.exists(target)) {
    stop(sprintf("lab_source(\"%s\"): file does not exist at %s.", rel, target),
         call. = FALSE)
  }
  source(target, local = FALSE, chdir = FALSE)
  invisible(NULL)
}

# ── Artifact helpers ──────────────────────────────────────────────────

upstream <- function(slug, filename) {
  file.path(lab_output_root(), slug, filename)
}

load_parquet <- function(slug, filename = "trials.parquet") {
  if (!requireNamespace("arrow", quietly = TRUE)) {
    stop("load_parquet() requires the `arrow` package. ",
         "Install via `install.packages(\"arrow\")`.", call. = FALSE)
  }
  arrow::read_parquet(upstream(slug, filename))
}

load_json <- function(slug, filename) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("load_json() requires the `jsonlite` package. ",
         "Install via `install.packages(\"jsonlite\")`.", call. = FALSE)
  }
  jsonlite::fromJSON(upstream(slug, filename), simplifyVector = FALSE)
}
