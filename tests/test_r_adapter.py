"""End-to-end tests for the R language adapter.

These tests require `Rscript` and `jsonlite` to be installed. They're
skipped (not failed) when R is missing — labdash itself is still
useful in pure-Python mode, and CI environments may not have R.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def _r_available() -> bool:
    if shutil.which("Rscript") is None:
        return False
    try:
        proc = subprocess.run(
            ["Rscript", "-e", "if (!requireNamespace('jsonlite', quietly=TRUE)) quit(status=1)"],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


needs_r = pytest.mark.skipif(
    not _r_available(),
    reason="Rscript + jsonlite required for R adapter tests",
)


@pytest.fixture
def r_collection(tmp_path: Path) -> Path:
    """Scaffold a tiny R collection with one stats-only analysis."""
    coll = tmp_path / "rcoll"
    (coll / "_lib").mkdir(parents=True)
    (coll / "ex").mkdir(parents=True)

    (coll / "collection.yaml").write_text("language: r\ntitle: Test\n")

    (coll / "_lib" / "helpers.R").write_text(textwrap.dedent("""\
        say_hello <- function(name) sprintf("hello, %s", name)
    """))

    (coll / "ex" / "meta.yaml").write_text(textwrap.dedent("""\
        title: Example
        group: Examples
        description: smoke test
        methodology: ""
        tags: []
        status: draft
        output_format: table
        dependencies: []
    """))
    (coll / "ex" / "analysis.R").write_text(textwrap.dedent("""\
        lab_source("helpers.R")

        run <- function(output_dir) {
          msg <- say_hello("labdash")
          writeLines("<table><tr><td>ok</td></tr></table>",
                     file.path(output_dir, "output.html"))
          list(message = msg, value = 42L)
        }
    """))
    (coll / "ex" / "notes.md").write_text("<!-- placeholder -->\n")
    return coll


@needs_r
def test_r_collection_discover_and_run(r_collection: Path, tmp_path: Path):
    """End-to-end: discover an R collection, run an analysis, check outputs."""
    from labdash.runner import discover_analyses, run_analysis

    analyses = discover_analyses(r_collection)
    assert len(analyses) == 1
    assert analyses[0]["slug"] == "ex"
    assert analyses[0]["language"].name == "r"
    assert analyses[0]["analysis_path"].name == "analysis.R"

    out_dir = tmp_path / "out"
    result = run_analysis(analyses[0], out_dir)

    assert result["success"], f"R analysis failed:\n{result['error']}"
    assert result["stats"]["message"] == "hello, labdash"
    assert result["stats"]["value"] == 42
    assert (out_dir / "ex" / "stats.json").is_file()
    assert (out_dir / "ex" / "output.html").is_file()


def test_r_import_parser_accepts_canonical():
    """`lab_source("foo.R")` resolves to `foo.R` when the file exists."""
    from labdash.r_import_parser import parse_r_lib_imports

    lib = Path(__file__).parent / "_r_parser_fixture_lib"
    lib.mkdir(exist_ok=True)
    (lib / "foo.R").write_text("# stub\n")
    try:
        wrapper = lib.parent / "_r_parser_fixture_wrapper.R"
        wrapper.write_text('lab_source("foo.R")\n')
        rels = parse_r_lib_imports(wrapper, lib_dir=lib)
        assert rels == {"foo.R"}
    finally:
        # Cleanup
        for p in lib.iterdir():
            p.unlink()
        lib.rmdir()
        wrapper = Path(__file__).parent / "_r_parser_fixture_wrapper.R"
        if wrapper.exists():
            wrapper.unlink()


def test_r_import_parser_rejects_dynamic(tmp_path: Path):
    """`lab_source(var)` with a non-literal arg raises LibImportError."""
    from labdash.r_import_parser import parse_r_lib_imports
    from labdash.lib_graph import LibImportError

    lib = tmp_path / "_lib"
    lib.mkdir()
    wrapper = tmp_path / "bad.R"
    wrapper.write_text('path <- "foo.R"\nlab_source(path)\n')

    with pytest.raises(LibImportError):
        parse_r_lib_imports(wrapper, lib_dir=lib)


def test_r_import_parser_rejects_bare_source_under_lib(tmp_path: Path):
    """`source("_lib/foo.R")` outside of lab_source is rejected."""
    from labdash.r_import_parser import parse_r_lib_imports
    from labdash.lib_graph import LibImportError

    lib = tmp_path / "_lib"
    lib.mkdir()
    wrapper = tmp_path / "bad.R"
    wrapper.write_text('source("_lib/foo.R")\n')

    with pytest.raises(LibImportError):
        parse_r_lib_imports(wrapper, lib_dir=lib)


def test_r_language_resolves_from_collection_yaml(tmp_path: Path):
    """`collection.yaml: language: r` resolves to RLanguage."""
    from labdash.languages import resolve_language_for, RLanguage

    coll = tmp_path / "x"
    coll.mkdir()
    (coll / "collection.yaml").write_text("language: r\n")

    assert resolve_language_for(coll) is RLanguage
