"""Unit tests for `labdash.live_translator`.

Each test builds a synthetic collection on disk, primes the translator,
then synthesizes one `FileChange` and asserts that the right SSE events
land on the bus. The watcher itself is not involved.

A `_StubBus` collects published events synchronously so tests can run
without an event loop.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from labdash.live_translator import LiveTranslator, diff_registry
from labdash.watcher import FileChange


# ── stub bus ─────────────────────────────────────────────────────────


class _StubBus:
    """Captures `publish` calls in-process; no asyncio involved."""

    def __init__(self):
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)

    def by_name(self, name: str) -> list[dict]:
        return [e for e in self.events if e.get("event") == name]


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def collection(tmp_path):
    """Synthetic collection with two slugs, a wrapper, _lib, registry."""
    root = tmp_path / "project"
    analyses = root / "analysis" / "coll"
    output = root / "_output" / "coll"
    analyses.mkdir(parents=True)
    output.mkdir(parents=True)

    # _lib
    lib = analyses / "_lib"
    lib.mkdir()
    (lib / "style.py").write_text("COLOR = 'blue'\n")
    (lib / "preprocessing.py").write_text(
        "from _lib import style\nVALUE = 1\n"
    )
    (lib / "plots").mkdir()
    (lib / "plots" / "sde.py").write_text(
        "from _lib import preprocessing\ndef make(): return preprocessing.VALUE\n"
    )

    # slug_a: imports plots/sde transitively via preprocessing.py.
    # Use fully-qualified `from _lib.plots.sde import make` — the parser
    # convention is to take everything after `_lib.` as the file path,
    # so `_lib.plots.sde` resolves to `plots/sde.py`.
    (analyses / "slug_a").mkdir()
    (analyses / "slug_a" / "analysis.py").write_text(
        "from _lib.plots.sde import make\n"
        "def run(output_dir):\n"
        "    return {'v': make()}\n"
    )
    (analyses / "slug_a" / "meta.yaml").write_text(yaml.safe_dump({
        "title": "A",
        "group": "g1",
        "status": "draft",
        "tags": ["t1"],
        "description": "desc A",
    }))

    # slug_b: imports preprocessing directly
    (analyses / "slug_b").mkdir()
    (analyses / "slug_b" / "analysis.py").write_text(
        "from _lib import preprocessing\n"
        "def run(output_dir):\n"
        "    return {'v': preprocessing.VALUE}\n"
    )
    (analyses / "slug_b" / "meta.yaml").write_text(yaml.safe_dump({
        "title": "B",
        "group": "g1",
        "status": "active",
        "tags": [],
        "description": "desc B",
    }))

    # registry — two groups, slug_a in g1, slug_b in g1 (so reorder possible)
    (analyses / "registry.yaml").write_text(yaml.safe_dump({
        "groups": [{"name": "g1", "analyses": ["slug_a", "slug_b"]}],
        "tags": ["t1"],
    }))

    # Pre-populate output dirs with mtimes slightly past the inputs'
    # creation time so slugs start non-stale. Tests then bump input
    # mtimes far enough into the future (`_bump_mtime` adds 60s) that
    # `is_stale` flips them, producing observable `stale_set` events.
    baseline = time.time() + 1.0
    for slug in ("slug_a", "slug_b"):
        sd = output / slug
        sd.mkdir(parents=True)
        out_file = sd / "output.png"
        out_file.write_bytes(b"\x89PNG")
        os.utime(out_file, (baseline, baseline))

    return analyses, output


def _prime(collection) -> tuple[LiveTranslator, _StubBus]:
    analyses, output = collection
    bus = _StubBus()
    tr = LiveTranslator(analyses, output, bus)
    tr.prime()
    bus.events.clear()  # discard whatever prime() emitted (none today)
    return tr, bus


def _bump_mtime(path: Path, *, delta_s: float = 60.0):
    """Force a future mtime so `is_stale` sees the change reliably.

    Default is 60s into the future — comfortably past the fixture's
    output-file baseline (now + 1s).
    """
    t = time.time() + delta_s
    os.utime(path, (t, t))


# ── parse + diff helpers ─────────────────────────────────────────────


def test_diff_registry_within_group_reorder():
    old = [{"name": "g1", "analyses": ["a", "b", "c"]}]
    new = [{"name": "g1", "analyses": ["c", "a", "b"]}]
    events = diff_registry(old, new)
    assert len(events) == 1
    assert events[0]["event"] == "card_order"
    assert events[0]["data"]["group"] == "g1"
    assert events[0]["data"]["slugs"] == ["c", "a", "b"]


def test_diff_registry_cross_group_move():
    old = [
        {"name": "g1", "analyses": ["a", "b"]},
        {"name": "g2", "analyses": ["c"]},
    ]
    new = [
        {"name": "g1", "analyses": ["a"]},
        {"name": "g2", "analyses": ["b", "c"]},
    ]
    events = diff_registry(old, new)
    moved = [e for e in events if e["event"] == "card_moved"]
    assert len(moved) == 1
    assert moved[0]["data"]["slug"] == "b"
    assert moved[0]["data"]["new_group"] == "g2"
    assert moved[0]["data"]["before_slug"] == "c"


def test_diff_registry_new_group_triggers_reload():
    old = [{"name": "g1", "analyses": ["a", "b"]}]
    new = [
        {"name": "g1", "analyses": ["a", "b"]},
        {"name": "g2", "analyses": []},
    ]
    events = diff_registry(old, new)
    assert events == [{"event": "full_reload", "data": {"reason": "group list changed"}}]


def test_diff_registry_slug_added_triggers_reload():
    old = [{"name": "g1", "analyses": ["a"]}]
    new = [{"name": "g1", "analyses": ["a", "new"]}]
    events = diff_registry(old, new)
    assert events == [{"event": "full_reload", "data": {"reason": "slug set changed"}}]


def test_diff_registry_no_change():
    same = [{"name": "g1", "analyses": ["a", "b"]}]
    assert diff_registry(same, same) == []


# ── translator handlers ──────────────────────────────────────────────


def test_lib_edit_emits_lib_code_and_stale_set(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    lib_file = analyses / "_lib" / "preprocessing.py"
    lib_file.write_text("from _lib import style\nVALUE = 2\n")  # content change
    _bump_mtime(lib_file)

    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    libs = bus.by_name("lib_code")
    assert len(libs) == 1
    assert libs[0]["data"]["path"] == "preprocessing.py"
    assert "VALUE = 2" in libs[0]["data"]["raw"]
    assert libs[0]["data"]["highlighted"]  # non-empty

    stale = bus.by_name("stale_set")
    assert len(stale) == 1
    # Both slugs transitively import preprocessing.py and have no output → stale.
    assert set(stale[0]["data"]["slugs"]) == {"slug_a", "slug_b"}


def test_lib_touch_without_content_change_no_lib_code(collection):
    """Touching mtime without changing content → no `lib_code` event."""
    analyses, output = collection
    tr, bus = _prime(collection)

    lib_file = analyses / "_lib" / "preprocessing.py"
    _bump_mtime(lib_file)  # only mtime change

    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    assert bus.by_name("lib_code") == []
    # Stale set wasn't previously emitted (prime() had emit=False), so the
    # first recompute emits it now.
    assert len(bus.by_name("stale_set")) == 1


def test_lib_error_propagates_and_clears(collection):
    """Star import in `_lib` → `lib_error`; clean again → `lib_error_clear`."""
    analyses, output = collection
    tr, bus = _prime(collection)

    lib_file = analyses / "_lib" / "preprocessing.py"
    # Inject star import — banned by lib_graph.
    lib_file.write_text("from _lib import style\nfrom _lib.style import *\n")
    _bump_mtime(lib_file)

    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    errors = bus.by_name("lib_error")
    assert len(errors) == 1
    assert "import *" in errors[0]["data"]["message"]

    bus.events.clear()
    # Restore clean version.
    lib_file.write_text("from _lib import style\nVALUE = 3\n")
    _bump_mtime(lib_file)
    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))
    assert bus.by_name("lib_error_clear")


def test_wrapper_edit_emits_wrapper_code_and_stale_set(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    wrapper = analyses / "slug_a" / "analysis.py"
    wrapper.write_text(
        "from _lib.plots.sde import make\n"
        "def run(output_dir):\n"
        "    return {'v': make() + 1}\n"
    )
    _bump_mtime(wrapper)

    tr.handle(FileChange(path=wrapper, kind="wrapper", slug="slug_a", is_delete=False))

    wcs = bus.by_name("wrapper_code")
    assert len(wcs) == 1
    assert wcs[0]["data"]["slug"] == "slug_a"
    assert "+ 1" in wcs[0]["data"]["raw"]
    assert bus.by_name("stale_set")


def test_meta_status_change_emits_meta_changed(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    meta = analyses / "slug_a" / "meta.yaml"
    data = yaml.safe_load(meta.read_text())
    data["status"] = "active"
    meta.write_text(yaml.safe_dump(data))

    tr.handle(FileChange(path=meta, kind="meta", slug="slug_a", is_delete=False))

    mc = bus.by_name("meta_changed")
    assert len(mc) == 1
    assert mc[0]["data"]["slug"] == "slug_a"
    assert mc[0]["data"]["fields"] == {"status": "active"}
    assert not bus.by_name("full_reload")


def test_meta_dependencies_change_triggers_reload(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    meta = analyses / "slug_a" / "meta.yaml"
    data = yaml.safe_load(meta.read_text())
    data["dependencies"] = ["slug_b"]
    meta.write_text(yaml.safe_dump(data))

    tr.handle(FileChange(path=meta, kind="meta", slug="slug_a", is_delete=False))

    assert bus.by_name("full_reload")
    assert not bus.by_name("meta_changed")


def test_meta_unchanged_emits_nothing_surgical(collection):
    """Saving meta.yaml with no semantic change → no meta_changed event."""
    analyses, output = collection
    tr, bus = _prime(collection)

    meta = analyses / "slug_a" / "meta.yaml"
    # Rewrite identical content
    data = yaml.safe_load(meta.read_text())
    meta.write_text(yaml.safe_dump(data))

    tr.handle(FileChange(path=meta, kind="meta", slug="slug_a", is_delete=False))

    assert bus.by_name("meta_changed") == []
    # stale_set may or may not appear; depends on prior emit. Don't assert.


def test_registry_within_group_reorder_emits_card_order(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    reg_path = analyses / "registry.yaml"
    new_reg = yaml.safe_load(reg_path.read_text())
    new_reg["groups"][0]["analyses"] = ["slug_b", "slug_a"]  # swap order
    reg_path.write_text(yaml.safe_dump(new_reg))

    tr.handle(FileChange(path=reg_path, kind="registry", slug=None, is_delete=False))

    co = bus.by_name("card_order")
    assert len(co) == 1
    assert co[0]["data"]["group"] == "g1"
    assert co[0]["data"]["slugs"] == ["slug_b", "slug_a"]
    assert not bus.by_name("full_reload")


def test_registry_group_added_triggers_reload(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    reg_path = analyses / "registry.yaml"
    new_reg = yaml.safe_load(reg_path.read_text())
    new_reg["groups"].append({"name": "g2", "analyses": []})
    reg_path.write_text(yaml.safe_dump(new_reg))

    tr.handle(FileChange(path=reg_path, kind="registry", slug=None, is_delete=False))

    assert bus.by_name("full_reload")


def test_wrapper_delete_triggers_reload(collection):
    analyses, output = collection
    tr, bus = _prime(collection)

    wrapper = analyses / "slug_a" / "analysis.py"
    tr.handle(FileChange(path=wrapper, kind="wrapper", slug="slug_a", is_delete=True))

    assert bus.by_name("full_reload")


def test_lib_edit_clears_sys_modules(collection):
    """Mirror save_lib_code: `_lib` keys in sys.modules are removed."""
    import sys

    analyses, output = collection
    tr, bus = _prime(collection)

    # Plant a fake _lib module in sys.modules
    sys.modules["_lib.fake_module"] = object()
    sys.modules["_lib"] = object()

    lib_file = analyses / "_lib" / "preprocessing.py"
    lib_file.write_text("from _lib import style\nVALUE = 99\n")
    _bump_mtime(lib_file)

    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    assert "_lib.fake_module" not in sys.modules
    assert "_lib" not in sys.modules


def test_stale_set_idempotent(collection):
    """Repeated identical recomputes don't re-emit `stale_set`."""
    analyses, output = collection
    tr, bus = _prime(collection)

    lib_file = analyses / "_lib" / "preprocessing.py"
    lib_file.write_text("VALUE = 2\n")  # remove the style import
    _bump_mtime(lib_file)
    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    first_stale = bus.by_name("stale_set")
    assert len(first_stale) == 1

    bus.events.clear()
    # Another tick that doesn't change the stale set
    _bump_mtime(lib_file)
    tr.handle(FileChange(path=lib_file, kind="lib", slug=None, is_delete=False))

    assert bus.by_name("stale_set") == []  # idempotent


# ── agent_active marker handler ──────────────────────────────────────


def test_agent_active_create_with_timestamp(collection):
    """Valid YAML marker → `agent_active` event carrying the timestamp;
    state field populated; `get_agent_active()` returns the same dict."""
    analyses, output = collection
    tr, bus = _prime(collection)

    marker = analyses / ".agent_active"
    marker.write_text("edits_start_at: 2026-05-11T14:32:10Z\n")

    tr.handle(FileChange(path=marker, kind="agent_active", slug=None, is_delete=False))

    events = bus.by_name("agent_active")
    assert len(events) == 1
    assert events[0]["data"]["edits_start_at"] == "2026-05-11T14:32:10Z"
    assert tr.get_agent_active() == {"edits_start_at": "2026-05-11T14:32:10Z"}


def test_agent_active_create_empty_file(collection):
    """Empty marker file → `agent_active` event with `edits_start_at: None`."""
    analyses, output = collection
    tr, bus = _prime(collection)

    marker = analyses / ".agent_active"
    marker.write_text("")

    tr.handle(FileChange(path=marker, kind="agent_active", slug=None, is_delete=False))

    events = bus.by_name("agent_active")
    assert len(events) == 1
    assert events[0]["data"]["edits_start_at"] is None
    assert tr.get_agent_active() == {"edits_start_at": None}


def test_agent_active_create_malformed_yaml(collection):
    """Malformed YAML → no crash; `agent_active` event with timestamp None."""
    analyses, output = collection
    tr, bus = _prime(collection)

    marker = analyses / ".agent_active"
    marker.write_text(":\n: : not yaml :\n")

    tr.handle(FileChange(path=marker, kind="agent_active", slug=None, is_delete=False))

    events = bus.by_name("agent_active")
    assert len(events) == 1
    assert events[0]["data"]["edits_start_at"] is None


def test_agent_active_delete_emits_idle(collection):
    """Removing the marker → `agent_idle` event; state cleared."""
    analyses, output = collection
    tr, bus = _prime(collection)

    marker = analyses / ".agent_active"
    marker.write_text("edits_start_at: 2026-05-11T14:32:10Z\n")
    tr.handle(FileChange(path=marker, kind="agent_active", slug=None, is_delete=False))
    bus.events.clear()

    marker.unlink()
    tr.handle(FileChange(path=marker, kind="agent_active", slug=None, is_delete=True))

    assert bus.by_name("agent_idle")
    assert tr.get_agent_active() is None


def test_prime_picks_up_existing_marker(collection):
    """Marker on disk at `prime()` time → state populated; no event emitted."""
    analyses, output = collection
    marker = analyses / ".agent_active"
    marker.write_text("edits_start_at: 2026-05-11T14:00:00Z\n")

    bus = _StubBus()
    tr = LiveTranslator(analyses, output, bus)
    tr.prime()

    # prime() does not publish; initial state is delivered via `hello`.
    assert bus.by_name("agent_active") == []
    assert tr.get_agent_active() == {"edits_start_at": "2026-05-11T14:00:00Z"}
