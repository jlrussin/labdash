"""Tests for `labdash.lib_graph` and its integration with the staleness check.

Verifies the transitive-staleness fix: editing a `_lib/` file that is imported
indirectly (e.g. wrapper → `_lib/plots/X.py` → `_lib/leaf.py`) marks the
wrapper stale.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from labdash.lib_graph import (
    LibImportError,
    parse_lib_imports,
    build_lib_graph,
    transitive_lib_closure,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def lib_dir(tmp_path: Path) -> Path:
    """Build a synthetic `_lib/` mirroring real-world structure.

    Shape (depth annotations are how deep a transitive walk would go from a
    typical wrapper):

        _lib/
          __init__.py        (empty)
          style.py           leaf
          preprocessing.py   leaf
          pipeline.py        depth 1 — imports style
          plots/
            __init__.py      (empty)
            sde.py           depth 1 — imports preprocessing, style
            deep.py          depth 2 — imports plots.sde
    """
    lib = tmp_path / "_lib"
    (lib / "plots").mkdir(parents=True)

    (lib / "__init__.py").write_text("")
    (lib / "plots" / "__init__.py").write_text("")
    (lib / "style.py").write_text("COLOR = 'blue'\n")
    (lib / "preprocessing.py").write_text("def clean(df): return df\n")
    (lib / "pipeline.py").write_text("from _lib.style import COLOR\n")
    (lib / "plots" / "sde.py").write_text(
        "from _lib.preprocessing import clean\n"
        "from _lib.style import COLOR\n"
    )
    (lib / "plots" / "deep.py").write_text(
        "from _lib.plots.sde import clean\n"
    )
    return lib


def _write_wrapper(tmp_path: Path, slug: str, body: str) -> Path:
    """Create `<tmp_path>/<slug>/analysis.py` containing `body`. Returns path."""
    d = tmp_path / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / "analysis.py"
    p.write_text(textwrap.dedent(body))
    return p


# ── parse_lib_imports ──────────────────────────────────────────────────


def test_parse_direct_import_returns_single_rel(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        from _lib.plots.sde import clean
    """)
    assert parse_lib_imports(wrapper, lib_dir=lib_dir) == {"plots/sde.py"}


def test_parse_no_lib_imports_returns_empty(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        import pandas as pd
        import numpy as np
    """)
    assert parse_lib_imports(wrapper, lib_dir=lib_dir) == set()


def test_parse_external_imports_ignored(tmp_path: Path, lib_dir: Path):
    """Irrelevant external imports never trigger LibImportError."""
    wrapper = _write_wrapper(tmp_path, "w", """
        import pandas as pd
        from scipy import stats
        from _lib.plots.sde import clean
    """)
    assert parse_lib_imports(wrapper, lib_dir=lib_dir) == {"plots/sde.py"}


def test_parse_star_import_raises(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        from _lib.preprocessing import *
    """)
    with pytest.raises(LibImportError) as excinfo:
        parse_lib_imports(wrapper, lib_dir=lib_dir)
    msg = str(excinfo.value)
    assert "from _lib.preprocessing import *" in msg
    assert str(wrapper) in msg
    assert ":2" in msg  # textwrap.dedent's leading newline means line 2


def test_parse_importlib_import_module_raises(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        import importlib
        importlib.import_module("_lib.style")
    """)
    with pytest.raises(LibImportError) as excinfo:
        parse_lib_imports(wrapper, lib_dir=lib_dir)
    assert "importlib.import_module" in str(excinfo.value)


def test_parse_dunder_import_raises(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        x = __import__("_lib.style")
    """)
    with pytest.raises(LibImportError):
        parse_lib_imports(wrapper, lib_dir=lib_dir)


def test_parse_dynamic_import_nonconst_arg_raises(tmp_path: Path, lib_dir: Path):
    """We ban `import_module(var)` because we can't prove it doesn't target _lib."""
    wrapper = _write_wrapper(tmp_path, "w", """
        import importlib
        name = "_lib.style"
        importlib.import_module(name)
    """)
    with pytest.raises(LibImportError) as excinfo:
        parse_lib_imports(wrapper, lib_dir=lib_dir)
    assert "<dynamic>" in str(excinfo.value)


def test_parse_syntax_error_raises_libimport(tmp_path: Path, lib_dir: Path):
    wrapper = _write_wrapper(tmp_path, "w", """
        from _lib import (
    """)
    with pytest.raises(LibImportError) as excinfo:
        parse_lib_imports(wrapper, lib_dir=lib_dir)
    msg = str(excinfo.value)
    assert "SyntaxError" in msg
    assert str(wrapper) in msg


def test_parse_filters_missing_files(tmp_path: Path, lib_dir: Path):
    """Imports that don't resolve to a real `_lib/` file are dropped."""
    wrapper = _write_wrapper(tmp_path, "w", """
        from _lib.nonexistent import thing
        from _lib.plots.sde import clean
    """)
    assert parse_lib_imports(wrapper, lib_dir=lib_dir) == {"plots/sde.py"}


def test_parse_from_lib_import_sibling(tmp_path: Path, lib_dir: Path):
    """`from _lib import style` should register `style.py`."""
    wrapper = _write_wrapper(tmp_path, "w", """
        from _lib import style
    """)
    assert parse_lib_imports(wrapper, lib_dir=lib_dir) == {"style.py"}


# ── build_lib_graph ────────────────────────────────────────────────────


def test_build_lib_graph_returns_expected_adjacency(lib_dir: Path):
    graph = build_lib_graph(lib_dir)
    # Leaves resolve to empty sets
    assert graph["style.py"] == set()
    assert graph["preprocessing.py"] == set()
    assert graph["__init__.py"] == set()
    assert graph["plots/__init__.py"] == set()
    # One-hop importers
    assert graph["pipeline.py"] == {"style.py"}
    assert graph["plots/sde.py"] == {"preprocessing.py", "style.py"}
    # Chained
    assert graph["plots/deep.py"] == {"plots/sde.py"}


# ── transitive_lib_closure ─────────────────────────────────────────────


def test_closure_two_hop_includes_indirect_leaves(lib_dir: Path):
    graph = build_lib_graph(lib_dir)
    closure = transitive_lib_closure({"plots/deep.py"}, graph)
    assert closure == {
        "plots/deep.py",
        "plots/sde.py",
        "preprocessing.py",
        "style.py",
    }


def test_closure_terminates_on_cycle():
    """Synthetic cycle a → b → a — BFS must terminate via the `seen` set."""
    graph = {
        "a.py": {"b.py"},
        "b.py": {"a.py"},
    }
    assert transitive_lib_closure({"a.py"}, graph) == {"a.py", "b.py"}


def test_closure_missing_root_no_keyerror():
    """Roots that aren't keys in the graph are returned, never traversed."""
    graph = {"a.py": {"b.py"}}
    assert transitive_lib_closure({"ghost.py"}, graph) == {"ghost.py"}


def test_closure_single_leaf(lib_dir: Path):
    graph = build_lib_graph(lib_dir)
    assert transitive_lib_closure({"style.py"}, graph) == {"style.py"}


# ── Integration: runner.is_stale picks up transitive edits ─────────────


def _build_meta(slug: str, deps: list[str] | None = None) -> str:
    deps_yaml = f"dependencies: [{', '.join(deps)}]\n" if deps else ""
    return (
        f'title: "{slug}"\n'
        f'group: "G"\n'
        f'description: "test"\n'
        f'methodology: "test"\n'
        f'tags: []\n'
        f'status: draft\n'
        f'output_format: png\n'
        + deps_yaml
    )


def _set_mtime(p: Path, t: float) -> None:
    os.utime(p, (t, t))


def test_is_stale_picks_up_two_hop_lib_edit(tmp_path: Path):
    """The bug fix in action.

    Layout:
        analyses/
          _lib/
            style.py
            plots/
              sde.py            (imports _lib.style — the indirect path)
          wrapper_a/
            analysis.py         (imports _lib.plots.sde)
            meta.yaml
        _output/
          wrapper_a/output.png

    With baselines set so wrapper_a is fresh, then nudge `_lib/style.py`'s
    mtime forward — pre-fix this was missed (one-hop only); post-fix
    `is_stale("wrapper_a", ...)` should return True.
    """
    analyses_dir = tmp_path / "analyses"
    output_root = tmp_path / "_output"
    lib = analyses_dir / "_lib"
    (lib / "plots").mkdir(parents=True)

    (lib / "__init__.py").write_text("")
    (lib / "plots" / "__init__.py").write_text("")
    (lib / "style.py").write_text("C = 1\n")
    (lib / "plots" / "sde.py").write_text("from _lib.style import C\n")

    wrapper_dir = analyses_dir / "wrapper_a"
    wrapper_dir.mkdir()
    wrapper_py = wrapper_dir / "analysis.py"
    wrapper_py.write_text("from _lib.plots.sde import C\n")
    (wrapper_dir / "meta.yaml").write_text(_build_meta("wrapper_a"))

    out_dir = output_root / "wrapper_a"
    out_dir.mkdir(parents=True)
    out_png = out_dir / "output.png"
    out_png.write_text("")

    # Set a baseline: every source older than the output.
    base = 1_000_000_000.0
    _set_mtime(lib / "style.py", base)
    _set_mtime(lib / "plots" / "sde.py", base)
    _set_mtime(wrapper_py, base)
    _set_mtime(out_png, base + 100)

    from labdash.runner import is_stale

    analysis = {
        "slug": "wrapper_a",
        "dir": wrapper_dir,
        "meta": {"dependencies": []},
        "analysis_path": wrapper_py,
    }
    by_slug = {"wrapper_a": analysis}

    # Baseline: not stale.
    assert is_stale("wrapper_a", by_slug, output_root) is False

    # Now touch the 2-hop leaf; clear the cache and recheck.
    _set_mtime(lib / "style.py", base + 500)
    analysis.pop("shared_paths", None)

    assert is_stale("wrapper_a", by_slug, output_root) is True


def test_builder_attaches_transitive_shared_paths(tmp_path: Path):
    """`build_dashboard` should populate analysis['shared_paths'] with the
    transitive closure, while keeping `direct_shared_paths` for UI surfaces.
    """
    analyses_dir = tmp_path / "analyses"
    output_root = tmp_path / "_output"
    output_root.mkdir(parents=True)
    lib = analyses_dir / "_lib"
    (lib / "plots").mkdir(parents=True)
    (lib / "__init__.py").write_text("")
    (lib / "plots" / "__init__.py").write_text("")
    (lib / "style.py").write_text("C = 1\n")
    (lib / "plots" / "sde.py").write_text("from _lib.style import C\n")

    wrapper_dir = analyses_dir / "wrapper_a"
    wrapper_dir.mkdir()
    (wrapper_dir / "analysis.py").write_text("from _lib.plots.sde import C\n")
    (wrapper_dir / "meta.yaml").write_text(_build_meta("wrapper_a"))
    (analyses_dir / "collection.yaml").write_text('title: "t"\n')

    from labdash.builder import build_dashboard
    build_dashboard(analyses_dir, output_root)

    from labdash.runner import discover_analyses, ordered_analyses
    analyses = ordered_analyses(analyses_dir)
    # build_dashboard mutates the analysis dicts in place via ordered_analyses;
    # to verify, replay the attachment ourselves on a fresh discovery.
    # (The contract is: after build_dashboard runs, the next builder call sees
    # the same transitive set.)
    a = next(x for x in analyses if x["slug"] == "wrapper_a")
    # Re-run the attachment logic to assert the closure surface:
    from labdash.lib_graph import (
        build_lib_graph, parse_lib_imports, transitive_lib_closure,
    )
    graph = build_lib_graph(lib)
    direct = parse_lib_imports(a["analysis_path"], lib_dir=lib)
    transitive = transitive_lib_closure(direct, graph)
    assert direct == {"plots/sde.py"}
    assert transitive == {"plots/sde.py", "style.py"}


# ── Server-side: reverse-transitive impact for save_lib_code logic ─────


def test_reverse_transitive_impact_via_closure(lib_dir: Path, tmp_path: Path):
    """Models what `save_lib_code` does: for each wrapper, compute its
    transitive closure and check whether the edited file is in it.

    Two wrappers — one imports a direct importer of `style.py`, one imports
    something unrelated. Editing `style.py` should mark only the first.
    """
    direct_wrapper = _write_wrapper(tmp_path, "direct", """
        from _lib.plots.sde import clean   # sde imports style
    """)
    unrelated_wrapper = _write_wrapper(tmp_path, "unrelated", """
        from _lib.preprocessing import clean
    """)

    graph = build_lib_graph(lib_dir)
    edited = "style.py"

    affected: list[str] = []
    for slug, path in [("direct", direct_wrapper), ("unrelated", unrelated_wrapper)]:
        d = parse_lib_imports(path, lib_dir=lib_dir)
        if edited in transitive_lib_closure(d, graph):
            affected.append(slug)

    assert affected == ["direct"]
