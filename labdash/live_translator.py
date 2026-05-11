"""Translate filesystem changes into SSE events for the live-mode client.

The watcher (`watcher.py`) emits `FileChange` events; this module decides
*what* the client should be told and publishes typed events through the
event bus (`live_events.py`). One side of the wall touches the disk; the
other side speaks SSE.

Each event has the shape `{"event": <name>, "data": <dict>}`. The HTTP
SSE encoder in `server.py` turns those into `event: ...\\ndata: ...\\n\\n`
lines on the wire.

Key responsibilities:
  - Compute the stale set after any wrapper / `_lib` / meta change and
    push `stale_set` if it differs from the last-emitted set.
  - Diff the parsed registry to distinguish within-group reordering
    (`card_order`), cross-group moves (`card_moved`), and structural
    changes that warrant `full_reload`.
  - Diff parsed `meta.yaml` to emit `meta_changed` for surgically
    patchable fields and `full_reload` for everything else.
  - Suppress no-op UI events (`lib_code`, `wrapper_code`, `meta_changed`)
    via a per-file content-hash gate so editor "touch" events don't
    overwrite Monaco buffers.
  - Clear `_lib` from `sys.modules` on every `_lib` edit so the next run
    picks up the new code (mirrors `server.py:save_lib_code`).
"""

from __future__ import annotations

import hashlib
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import agent_context
from .builder import _highlight_python, _resolve_lib_dir
from .lib_graph import (
    LibImportError,
    build_lib_graph,
    parse_lib_imports,
    transitive_lib_closure,
)
from .live_events import EventBus
from .runner import discover_analyses, is_stale, load_registry
from .watcher import FileChange


# Meta fields that the in-DOM card surface can update in place.
_META_SURGICAL_FIELDS = (
    "title",
    "description",
    "methodology",
    "status",
    "tags",
    "figure_id",
    "caption",
)

# Meta fields that affect rendering structure (pipelines, deps).
_META_RELOAD_FIELDS = ("dependencies", "output_format")


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _safe_read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _normalize_groups(registry: dict | None) -> list[dict]:
    """Return groups as a list of `{name, analyses}` dicts.

    New-format registries already match; missing / old-format registries
    return `[]` (the diff layer treats this as "anything new → reload").
    """
    if not isinstance(registry, dict):
        return []
    groups = registry.get("groups")
    if not isinstance(groups, list) or (groups and not isinstance(groups[0], dict)):
        return []
    out: list[dict] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        name = g.get("name")
        analyses = g.get("analyses", [])
        if isinstance(name, str) and isinstance(analyses, list):
            out.append({"name": name, "analyses": list(analyses)})
    return out


def _read_meta(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def diff_registry(old: list[dict], new: list[dict]) -> list[dict]:
    """Compare two normalized group lists; return SSE event dicts.

    Rules:
      - Group list (names or order) changed → `full_reload`.
      - Slug set changed (added or removed across the whole registry) →
        `full_reload`. (A new slug needs a new card; surgical rendering
        is out of scope.)
      - Otherwise emit `card_moved` for slugs whose group changed and
        `card_order` for groups whose membership matches but ordering
        differs.
    """
    old_names = [g["name"] for g in old]
    new_names = [g["name"] for g in new]
    if old_names != new_names:
        return [{"event": "full_reload", "data": {"reason": "group list changed"}}]

    old_group_for: dict[str, str] = {
        s: g["name"] for g in old for s in g["analyses"]
    }
    new_group_for: dict[str, str] = {
        s: g["name"] for g in new for s in g["analyses"]
    }
    if set(old_group_for) != set(new_group_for):
        return [{"event": "full_reload", "data": {"reason": "slug set changed"}}]

    events: list[dict] = []

    # Cross-group moves
    moved: list[str] = [
        s for s in new_group_for
        if old_group_for[s] != new_group_for[s]
    ]
    new_by_name = {g["name"]: g for g in new}
    for slug in moved:
        target = new_by_name[new_group_for[slug]]
        idx = target["analyses"].index(slug)
        before = (
            target["analyses"][idx + 1]
            if idx + 1 < len(target["analyses"])
            else None
        )
        events.append({
            "event": "card_moved",
            "data": {
                "slug": slug,
                "new_group": new_group_for[slug],
                "group_label": new_group_for[slug],
                "before_slug": before,
            },
        })

    # Within-group reorders (only when membership unchanged)
    for new_g, old_g in zip(new, old):
        if new_g["name"] != old_g["name"]:
            continue
        if set(new_g["analyses"]) != set(old_g["analyses"]):
            continue
        if new_g["analyses"] != old_g["analyses"]:
            events.append({
                "event": "card_order",
                "data": {
                    "group": new_g["name"],
                    "slugs": list(new_g["analyses"]),
                },
            })

    return events


@dataclass
class _State:
    """Snapshot of disk state used to gate events and diff registry."""

    content_hashes: dict[Path, str] = field(default_factory=dict)
    meta_by_slug: dict[str, dict] = field(default_factory=dict)
    registry_groups: list[dict] = field(default_factory=list)
    stale_set: list[str] = field(default_factory=list)
    lib_error_active: bool = False


class LiveTranslator:
    """Owns the in-memory state needed to translate `FileChange` events.

    Construct once per `labdash serve` run; call `prime()` after the
    event loop is bound to the bus so any startup-emitted events can
    reach connected clients. `handle(change)` is called from the
    watcher's debounce-timer thread; it publishes events through the
    bus.
    """

    def __init__(
        self,
        analyses_dir: Path,
        output_dir: Path,
        bus: EventBus,
    ):
        self.analyses_dir = analyses_dir
        self.output_dir = output_dir
        self.bus = bus
        self._state = _State()
        # `handle` may run on multiple debounce threads if many paths
        # change simultaneously; guard the state mutations.
        self._lock = threading.Lock()

    # ── public API ─────────────────────────────────────────────────────

    def prime(self) -> None:
        """Load initial disk snapshot. Called once at server start."""
        with self._lock:
            self._refresh_registry_snapshot()
            self._refresh_meta_snapshot()
            self._refresh_lib_hashes()
            self._refresh_wrapper_hashes()
            self._recompute_stale_set(emit=False)

    def handle(self, change: FileChange) -> None:
        """Translate one filesystem change to zero or more SSE events."""
        with self._lock:
            try:
                if change.kind == "lib":
                    self._handle_lib(change)
                elif change.kind == "wrapper":
                    self._handle_wrapper(change)
                elif change.kind == "meta":
                    self._handle_meta(change)
                elif change.kind == "registry":
                    self._handle_registry(change)
                # `structural` is not currently emitted by the watcher;
                # if we add it later, route through `_emit_full_reload`.
            except Exception:
                # Last-ditch: don't let translator bugs hang the watcher.
                import traceback
                traceback.print_exc()

            if change.kind in ("meta", "registry"):
                try:
                    agent_context.sync(self.analyses_dir)
                except Exception:
                    import traceback
                    traceback.print_exc()

    # ── per-kind handlers ─────────────────────────────────────────────

    def _handle_lib(self, change: FileChange) -> None:
        if change.is_delete:
            self._emit_full_reload("_lib file removed")
            return

        # Invalidate import cache so a subsequent run sees the new code.
        for k in [k for k in sys.modules if "_lib" in k]:
            del sys.modules[k]

        new_text = _safe_read_text(change.path)
        if new_text is None:
            return

        new_hash = _hash_text(new_text)
        old_hash = self._state.content_hashes.get(change.path)
        content_changed = old_hash != new_hash
        if content_changed:
            self._state.content_hashes[change.path] = new_hash
            lib_dir = _resolve_lib_dir(self.analyses_dir)
            if lib_dir is not None:
                try:
                    rel = change.path.resolve().relative_to(lib_dir.resolve()).as_posix()
                except ValueError:
                    rel = change.path.name
                self.bus.publish({
                    "event": "lib_code",
                    "data": {
                        "path": rel,
                        "raw": new_text,
                        "highlighted": _highlight_python(new_text),
                    },
                })

        # mtime alone makes is_stale flip; recompute unconditionally.
        self._recompute_stale_set(emit=True)

    def _handle_wrapper(self, change: FileChange) -> None:
        if change.is_delete:
            self._emit_full_reload("wrapper file removed")
            return

        new_text = _safe_read_text(change.path)
        if new_text is None:
            return

        new_hash = _hash_text(new_text)
        old_hash = self._state.content_hashes.get(change.path)
        if old_hash != new_hash:
            self._state.content_hashes[change.path] = new_hash
            self.bus.publish({
                "event": "wrapper_code",
                "data": {
                    "slug": change.slug,
                    "raw": new_text,
                    "highlighted": _highlight_python(new_text),
                },
            })

        self._recompute_stale_set(emit=True)

    def _handle_meta(self, change: FileChange) -> None:
        if change.is_delete:
            self._emit_full_reload("meta.yaml removed")
            return

        new_meta = _read_meta(change.path)
        if new_meta is None:
            return

        old_meta = self._state.meta_by_slug.get(change.slug, {})

        # Reload-fields force a full reload (affects DOM structure).
        for k in _META_RELOAD_FIELDS:
            if old_meta.get(k) != new_meta.get(k):
                self._state.meta_by_slug[change.slug] = new_meta
                self._emit_full_reload(f"meta.{k} changed for {change.slug}")
                return

        # Surgical fields → diff and emit only what changed.
        fields_changed: dict[str, Any] = {}
        for k in _META_SURGICAL_FIELDS:
            if old_meta.get(k) != new_meta.get(k):
                fields_changed[k] = new_meta.get(k)

        self._state.meta_by_slug[change.slug] = new_meta

        if fields_changed:
            self.bus.publish({
                "event": "meta_changed",
                "data": {
                    "slug": change.slug,
                    "fields": fields_changed,
                },
            })

        # Status changes can affect filter visibility but not staleness;
        # still cheap to recheck.
        self._recompute_stale_set(emit=True)

    def _handle_registry(self, change: FileChange) -> None:
        if change.is_delete:
            self._emit_full_reload("registry.yaml removed")
            return

        new_groups = _normalize_groups(load_registry(self.analyses_dir))
        events = diff_registry(self._state.registry_groups, new_groups)
        self._state.registry_groups = new_groups

        for ev in events:
            self.bus.publish(ev)
            if ev["event"] == "full_reload":
                return

        # Group/order changes don't change mtimes, but if a slug was
        # silently added/removed by sync we still want stale recompute.
        self._recompute_stale_set(emit=True)

    # ── helpers ────────────────────────────────────────────────────────

    def _emit_full_reload(self, reason: str) -> None:
        self.bus.publish({
            "event": "full_reload",
            "data": {"reason": reason},
        })

    def _recompute_stale_set(self, *, emit: bool) -> None:
        """Build the lib graph, attach closures, run `is_stale` over all
        slugs. If the resulting set differs from the last emitted, publish
        a `stale_set` event (when `emit=True`).

        On `LibImportError`, publish `lib_error` once until parse succeeds
        again — subsequent successful recomputes emit `lib_error_clear`.
        """
        try:
            analyses = discover_analyses(self.analyses_dir)
            lib_dir = _resolve_lib_dir(self.analyses_dir)
            if lib_dir is not None:
                graph = build_lib_graph(lib_dir)
                for a in analyses:
                    direct = parse_lib_imports(a["analysis_path"], lib_dir=lib_dir)
                    a["shared_paths"] = sorted(
                        transitive_lib_closure(direct, graph)
                    )
            by_slug = {a["slug"]: a for a in analyses}
            memo: dict[str, bool] = {}
            stale = sorted(
                s for s in by_slug
                if is_stale(s, by_slug, self.output_dir, memo)
            )
        except LibImportError as e:
            if not self._state.lib_error_active:
                self._state.lib_error_active = True
                self.bus.publish({
                    "event": "lib_error",
                    "data": {"message": str(e)},
                })
            return

        if self._state.lib_error_active:
            self._state.lib_error_active = False
            self.bus.publish({"event": "lib_error_clear", "data": {}})

        if emit and stale != self._state.stale_set:
            self.bus.publish({
                "event": "stale_set",
                "data": {"slugs": stale},
            })
        self._state.stale_set = stale

    # ── prime helpers ─────────────────────────────────────────────────

    def _refresh_registry_snapshot(self) -> None:
        self._state.registry_groups = _normalize_groups(
            load_registry(self.analyses_dir)
        )

    def _refresh_meta_snapshot(self) -> None:
        self._state.meta_by_slug = {}
        for child in self.analyses_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            meta_path = child / "meta.yaml"
            meta = _read_meta(meta_path)
            if meta is not None:
                self._state.meta_by_slug[child.name] = meta

    def _refresh_lib_hashes(self) -> None:
        lib_dir = _resolve_lib_dir(self.analyses_dir)
        if lib_dir is None:
            return
        for p in lib_dir.rglob("*.py"):
            if p.is_file():
                text = _safe_read_text(p)
                if text is not None:
                    self._state.content_hashes[p] = _hash_text(text)

    def _refresh_wrapper_hashes(self) -> None:
        for child in self.analyses_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            wrapper = child / "analysis.py"
            if wrapper.is_file():
                text = _safe_read_text(wrapper)
                if text is not None:
                    self._state.content_hashes[wrapper] = _hash_text(text)
