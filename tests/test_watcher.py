"""Unit tests for `labdash.watcher`.

The classifier is tested deterministically without an Observer thread.
A separate end-to-end test starts the watcher against a tmp tree to
confirm the watchdog → debouncer → callback path actually fires.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from labdash.watcher import FileChange, FileWatcher


def _make_collection(tmp_path: Path) -> Path:
    """Build a synthetic collection layout for classifier tests."""
    analyses = tmp_path / "analysis" / "coll"
    (analyses / "slug_a").mkdir(parents=True)
    (analyses / "slug_a" / "analysis.py").write_text("# wrapper\n")
    (analyses / "slug_a" / "meta.yaml").write_text("title: A\n")
    (analyses / "slug_a" / "notes.md").write_text("# notes\n")  # irrelevant file
    (analyses / "registry.yaml").write_text("groups: []\n")
    (analyses / "_lib").mkdir()
    (analyses / "_lib" / "style.py").write_text("# style\n")
    (analyses / "_lib" / "plots").mkdir()
    (analyses / "_lib" / "plots" / "sde.py").write_text("# sde\n")
    return analyses


def _make_collection_with_sibling_lib(tmp_path: Path) -> tuple[Path, Path]:
    """`_lib/` sibling to analyses_dir (one level up)."""
    root = tmp_path / "project"
    analyses = root / "analysis" / "coll"
    lib = root / "analysis" / "_lib"
    (analyses / "slug_a").mkdir(parents=True)
    (analyses / "slug_a" / "analysis.py").write_text("# wrapper\n")
    (analyses / "slug_a" / "meta.yaml").write_text("title: A\n")
    (analyses / "registry.yaml").write_text("groups: []\n")
    lib.mkdir(parents=True)
    (lib / "preprocessing.py").write_text("# pre\n")
    return analyses, lib


# ── classifier ───────────────────────────────────────────────────────


def test_classifies_wrapper(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None)
    change = w.classify(analyses / "slug_a" / "analysis.py")
    assert change is not None
    assert change.kind == "wrapper"
    assert change.slug == "slug_a"
    assert change.is_delete is False


def test_classifies_meta(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None)
    change = w.classify(analyses / "slug_a" / "meta.yaml")
    assert change is not None
    assert change.kind == "meta"
    assert change.slug == "slug_a"


def test_classifies_registry(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None)
    change = w.classify(analyses / "registry.yaml")
    assert change is not None
    assert change.kind == "registry"
    assert change.slug is None


def test_classifies_lib_top_level(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None, lib_dir=analyses / "_lib")
    change = w.classify(analyses / "_lib" / "style.py")
    assert change is not None
    assert change.kind == "lib"


def test_classifies_lib_nested(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None, lib_dir=analyses / "_lib")
    change = w.classify(analyses / "_lib" / "plots" / "sde.py")
    assert change is not None
    assert change.kind == "lib"


def test_lib_outside_analyses_dir(tmp_path):
    """When `_lib/` is a sibling of analyses_dir, classification still works."""
    analyses, lib = _make_collection_with_sibling_lib(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None, lib_dir=lib)
    change = w.classify(lib / "preprocessing.py")
    assert change is not None
    assert change.kind == "lib"


def test_lib_yaml_ignored(tmp_path):
    """Non-`.py` files under `_lib/` should be ignored (e.g. future style.yaml)."""
    analyses = _make_collection(tmp_path)
    (analyses / "_lib" / "style.yaml").write_text("color: red\n")
    w = FileWatcher(analyses, on_change=lambda c: None, lib_dir=analyses / "_lib")
    assert w.classify(analyses / "_lib" / "style.yaml") is None


def test_ignores_notes_md(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(analyses / "slug_a" / "notes.md") is None


def test_ignores_swap_files(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(analyses / "slug_a" / ".analysis.py.swp") is None
    assert w.classify(analyses / "slug_a" / "analysis.py~") is None
    assert w.classify(analyses / "slug_a" / "analysis.py.tmp") is None


def test_ignores_pycache(tmp_path):
    analyses = _make_collection(tmp_path)
    w = FileWatcher(analyses, on_change=lambda c: None, lib_dir=analyses / "_lib")
    (analyses / "_lib" / "__pycache__").mkdir()
    cached = analyses / "_lib" / "__pycache__" / "style.cpython-311.pyc"
    cached.write_text("")
    assert w.classify(cached) is None


def test_ignores_underscore_dirs(tmp_path):
    """Dirs starting with `_` (other than `_lib`) are out of scope."""
    analyses = _make_collection(tmp_path)
    (analyses / "_drafts").mkdir()
    f = analyses / "_drafts" / "analysis.py"
    f.write_text("")
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(f) is None


def test_ignores_outside_tree(tmp_path):
    analyses = _make_collection(tmp_path)
    other = tmp_path / "other.py"
    other.write_text("")
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(other) is None


def test_classifies_agent_active_marker(tmp_path):
    """`.agent_active` at the collection root is whitelisted through the
    dotfile filter and classified as kind `agent_active`."""
    analyses = _make_collection(tmp_path)
    marker = analyses / ".agent_active"
    marker.write_text("edits_start_at: 2026-05-11T14:00:00Z\n")
    w = FileWatcher(analyses, on_change=lambda c: None)
    change = w.classify(marker)
    assert change is not None
    assert change.kind == "agent_active"
    assert change.slug is None


def test_agent_active_inside_slug_is_ignored(tmp_path):
    """A `.agent_active` placed inside a slug directory is NOT whitelisted —
    only the exact basename at the collection root counts."""
    analyses = _make_collection(tmp_path)
    misplaced = analyses / "slug_a" / ".agent_active"
    misplaced.write_text("")
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(misplaced) is None


def test_other_dotfiles_at_root_still_ignored(tmp_path):
    """The whitelist is exact — other dotfiles at the root are still dropped."""
    analyses = _make_collection(tmp_path)
    other = analyses / ".agent_active.swp"
    other.write_text("")
    w = FileWatcher(analyses, on_change=lambda c: None)
    assert w.classify(other) is None
    ds_store = analyses / ".DS_Store"
    ds_store.write_text("")
    assert w.classify(ds_store) is None


# ── debouncer ────────────────────────────────────────────────────────


def test_debouncer_coalesces_rapid_events(tmp_path):
    """Many enqueues of the same path within debounce window → one delivery."""
    analyses = _make_collection(tmp_path)
    received: list[FileChange] = []
    event = threading.Event()

    def cb(c):
        received.append(c)
        event.set()

    w = FileWatcher(analyses, on_change=cb, debounce_s=0.05)
    target = analyses / "slug_a" / "analysis.py"
    change = w.classify(target)
    assert change is not None

    for _ in range(5):
        w._enqueue(change)

    assert event.wait(0.5), "callback never fired"
    assert len(received) == 1
    assert received[0].kind == "wrapper"


def test_debouncer_separate_paths_fire_independently(tmp_path):
    analyses = _make_collection(tmp_path)
    received: list[FileChange] = []
    lock = threading.Lock()

    def cb(c):
        with lock:
            received.append(c)

    w = FileWatcher(analyses, on_change=cb, debounce_s=0.05)
    a = w.classify(analyses / "slug_a" / "analysis.py")
    b = w.classify(analyses / "slug_a" / "meta.yaml")
    assert a is not None and b is not None

    w._enqueue(a)
    w._enqueue(b)
    time.sleep(0.2)

    kinds = sorted(c.kind for c in received)
    assert kinds == ["meta", "wrapper"]


# ── end-to-end with real Observer ────────────────────────────────────


def test_end_to_end_watcher_picks_up_writes(tmp_path):
    """Start a real Observer; modify a wrapper; expect a callback.

    macOS FSEvents may replay historical events from just before the
    observer started, so we drain after startup before issuing the
    real edit and then poll for the wrapper kind to arrive.
    """
    analyses = _make_collection(tmp_path)
    received: list[FileChange] = []
    lock = threading.Lock()

    def cb(c):
        with lock:
            received.append(c)

    w = FileWatcher(
        analyses,
        on_change=cb,
        lib_dir=analyses / "_lib",
        debounce_s=0.05,
    )
    w.start()
    try:
        # Allow FSEvents to register and flush any historical replay.
        time.sleep(0.5)
        with lock:
            received.clear()

        target = analyses / "slug_a" / "analysis.py"
        target.write_text("# wrapper edited\n")

        deadline = time.time() + 3.0
        while time.time() < deadline:
            with lock:
                if any(c.kind == "wrapper" and c.slug == "slug_a" for c in received):
                    break
            time.sleep(0.05)
        else:
            with lock:
                snapshot = list(received)
            raise AssertionError(
                f"wrapper event never arrived; received: {snapshot}"
            )
    finally:
        w.stop()
