# LabDash R bootstrap.
#
# Invoked by `labdash.r_runner.run()` as:
#     Rscript --vanilla run_analysis.R <user_analysis.R>
#
# This script sources the user's analysis script (which must define
# `run <- function(output_dir) { ... }`), runs it, and serialises the
# returned value to `stats.json` in the slug's output directory.
#
# Context comes through env vars set by the Python side:
#   LABDASH_COLLECTION_DIR    — collection root (contains collection.yaml)
#   LABDASH_OUTPUT_ROOT       — collection-level output root
#   LABDASH_OUTPUT_DIR        — this slug's output directory
#   LABDASH_LIB_DIR           — absolute path to `_lib/` (may be "")
#   LABDASH_COLLECTION_CONFIG — absolute path to collection.yaml (may be "")
#   LABDASH_SLUG              — this slug's name

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) {
  stop("LabDash R bootstrap: missing analysis.R path argument.", call. = FALSE)
}
analysis_path <- args[[1L]]

if (!file.exists(analysis_path)) {
  stop(sprintf("LabDash R bootstrap: analysis script not found: %s", analysis_path),
       call. = FALSE)
}

output_dir <- Sys.getenv("LABDASH_OUTPUT_DIR", unset = "")
if (!nzchar(output_dir)) {
  stop("LabDash R bootstrap: LABDASH_OUTPUT_DIR not set.", call. = FALSE)
}

# Resolve the bundled `labdash.R` runtime helper relative to this
# bootstrap script. R has no `__file__` but `commandArgs(FALSE)` carries
# a `--file=` entry pointing at the running script.
bootstrap_file_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE),
                          value = TRUE)[[1L]]
bootstrap_path <- normalizePath(sub("^--file=", "", bootstrap_file_arg))
runtime_bundled <- normalizePath(
  file.path(dirname(bootstrap_path), "..", "r_runtime", "labdash.R"),
  mustWork = FALSE
)

# Prefer the in-collection `_lib/labdash.R` (lets the user version the
# helper alongside their analyses) and fall back to the bundled helper.
lib_dir <- Sys.getenv("LABDASH_LIB_DIR", unset = "")
runtime_user <- if (nzchar(lib_dir)) file.path(lib_dir, "labdash.R") else ""

if (nzchar(runtime_user) && file.exists(runtime_user)) {
  source(runtime_user, local = FALSE, chdir = FALSE)
} else if (file.exists(runtime_bundled)) {
  source(runtime_bundled, local = FALSE, chdir = FALSE)
} else {
  stop(sprintf(
    "LabDash R bootstrap: could not find labdash.R runtime helper (looked at %s and %s).",
    runtime_user, runtime_bundled
  ), call. = FALSE)
}

# Source the user's analysis into the global environment so the
# defined `run` function is callable below.
source(analysis_path, local = FALSE, chdir = FALSE)

if (!exists("run", mode = "function")) {
  stop(sprintf(
    "LabDash R bootstrap: %s did not define a `run` function. Expected `run <- function(output_dir) { ... }`.",
    analysis_path
  ), call. = FALSE)
}

# Pass the env-derived path explicitly (rather than relying on the
# local `output_dir` binding above, which could be shadowed by helpers
# or user code sourced into the global env).
result <- run(Sys.getenv("LABDASH_OUTPUT_DIR"))

if (!is.null(result) && length(result) > 0L) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop(
      "LabDash R bootstrap: `jsonlite` is required to write stats.json. ",
      "Install via `install.packages(\"jsonlite\")`.",
      call. = FALSE
    )
  }
  writeLines(
    jsonlite::toJSON(result, auto_unbox = TRUE, na = "null",
                     null = "null", pretty = TRUE),
    file.path(output_dir, "stats.json")
  )
}

invisible(NULL)
