"""Tests for `labdash.cli._resolve_dirs` — output is collection-local & CWD-independent.

The historical bug: `output_dir` was anchored to `config["_root"]` (the current
working directory), so building/serving the same collection from a different cwd
produced a different `_output` location → stray duplicate output trees. The fix
anchors output to the (resolved) collection's `analyses_dir`, so the location is a
pure function of the collection path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from labdash.cli import _resolve_dirs


def _coll(tmp_path: Path) -> Path:
    d = tmp_path / "analysis" / "mycoll"
    d.mkdir(parents=True)
    return d


def test_cwd_independence(tmp_path):
    """Same collection reached from two different roots → identical output_dir."""
    _coll(tmp_path)
    # A: repo root + `-c analysis/mycoll`
    a_an, a_out = _resolve_dirs({"_root": tmp_path, "analyses_dir": "analysis/mycoll"})
    # B: cwd is the collection dir itself (collection.yaml in cwd → analyses_dir ".")
    b_an, b_out = _resolve_dirs({"_root": tmp_path / "analysis" / "mycoll", "analyses_dir": "."})

    expected = (tmp_path / "analysis" / "mycoll" / "_output").resolve()
    assert a_out == expected
    assert b_out == expected
    assert a_out == b_out
    assert a_an == b_an == (tmp_path / "analysis" / "mycoll").resolve()


def test_output_is_collection_local(tmp_path):
    """_output lives inside the collection, not anchored to a separate root."""
    _coll(tmp_path)
    analyses_dir, output_dir = _resolve_dirs({"_root": tmp_path, "analyses_dir": "analysis/mycoll"})
    assert output_dir.parent == analyses_dir
    assert output_dir.name == "_output"
    # It must NOT be a repo-root sibling of `analysis/`.
    assert output_dir != tmp_path / "_output"


def test_explicit_output_dir_is_collection_relative(tmp_path):
    _coll(tmp_path)
    _an, output_dir = _resolve_dirs(
        {"_root": tmp_path, "analyses_dir": "analysis/mycoll", "output_dir": "custom_out"}
    )
    assert output_dir == (tmp_path / "analysis" / "mycoll" / "custom_out").resolve()


def test_explicit_absolute_output_dir_is_honored(tmp_path):
    _coll(tmp_path)
    abs_out = tmp_path / "abs_out"
    _an, output_dir = _resolve_dirs(
        {"_root": tmp_path, "analyses_dir": "analysis/mycoll", "output_dir": str(abs_out)}
    )
    assert output_dir == abs_out.resolve()


def test_missing_collection_raises(tmp_path):
    with pytest.raises(SystemExit):
        _resolve_dirs({"_root": tmp_path})
