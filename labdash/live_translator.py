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


def _parse_agent_active(text: str) -> str | None:
    """Extract `edits_start_at` from the marker file, or None if absent/malformed.

    YAML auto-parses bare ISO 8601 timestamps (`edits_start_at: 2026-...Z`)
    into a `datetime`. We coerce back to an ISO 8601 string with a `Z`
    suffix for UTC so the client's `new Date()` always sees the same shape
    regardless of whether the agent quoted the value or not.
    """
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("edits_start_at")
    if isinstance(val, str):
        return val
    if hasattr(val, "isoformat"):
        s = val.isoformat()
        if s.endswith("+00:00"):
            s = s[:-6] + "Z"
        return s
    return None


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
    agent_active: dict | None = None
    # When the server is mid-rename of a group, this is set; the watcher
    # events for the N+1 writes (registry + each affected meta.yaml) are
    # absorbed silently and a single `group_renamed` event is published
    # when the rename endpoint clears it. Shape:
    #   {"old": "Performance", "new": "Performance metrics",
    #    "slugs": {"a", "b", ...}}
    pending_rename: dict | None = None


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
            self._prime_agent_active()

    def get_agent_active(self) -> dict | None:
        """Return a copy of the current agent_active state for the `hello` payload."""
        with self._lock:
            return dict(self._state.agent_active) if self._state.agent_active else None

    # ── group rename coordination ────────────────────────────────────────

    def begin_rename(self, old_name: str, new_name: str, slugs: set[str]) -> None:
        """Mark a group rename as in-flight before the endpoint writes disk.

        Eagerly updates the in-memory snapshot so the per-file watcher
        events that follow (registry rename + each meta.yaml rewrite)
        diff to no-ops against state. A single `group_renamed` event is
        published from `end_rename_and_publish` when the writes are done.
        """
        with self._lock:
            self._state.pending_rename = {
                "old": old_name,
                "new": new_name,
                "slugs": set(slugs),
            }
            # Rename the group entry in the snapshot (preserve position
            # and slug list verbatim).
            for g in self._state.registry_groups:
                if g["name"] == old_name:
                    g["name"] = new_name
                    break
            # Flip group field on each affected slug's cached meta.
            for slug in slugs:
                meta = self._state.meta_by_slug.get(slug)
                if meta is not None:
                    meta["group"] = new_name

    def end_rename_and_publish(self) -> None:
        """Clear the in-flight flag and publish the single `group_renamed`
        event. Safe to call from a `finally` clause; a no-op if no rename
        is active (e.g. the endpoint short-circuited on validation)."""
        with self._lock:
            pending = self._state.pending_rename
            if pending is None:
                return
            self._state.pending_rename = None
            self.bus.publish({
                "event": "group_renamed",
                "data": {
                    "old_name": pending["old"],
                    "new_name": pending["new"],
                },
            })
        try:
            agent_context.sync(self.analyses_dir)
        except Exception:
            import traceback
            traceback.print_exc()

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
                elif change.kind == "agent_active":
                    self._handle_agent_active(change)
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

        # Group change → emit `card_moved` and update the in-memory registry
        # snapshot so a subsequent `_handle_registry` (when the server wrote
        # the registry as part of the same change) sees a no-op diff.
        old_group = old_meta.get("group", "Ungrouped")
        new_group = new_meta.get("group", "Ungrouped")
        group_changed = old_group != new_group

        # Suppress the per-meta group flip that's part of a pending rename
        # batch; the rename endpoint will publish one `group_renamed` event
        # for the whole batch in `end_rename_and_publish`.
        pending = self._state.pending_rename
        in_pending_rename = (
            group_changed
            and pending is not None
            and change.slug in pending["slugs"]
            and old_group == pending["old"]
            and new_group == pending["new"]
        )

        # Surgical fields → diff and emit only what changed.
        fields_changed: dict[str, Any] = {}
        for k in _META_SURGICAL_FIELDS:
            if old_meta.get(k) != new_meta.get(k):
                fields_changed[k] = new_meta.get(k)

        self._state.meta_by_slug[change.slug] = new_meta

        if group_changed and not in_pending_rename:
            # If the registry watcher already moved this slug (we see the
            # snapshot reflects the new group), skip the duplicate event.
            current_group_in_snapshot = self._slug_group_in_snapshot(change.slug)
            if current_group_in_snapshot != new_group:
                self._move_slug_in_snapshot(change.slug, new_group)
                self.bus.publish({
                    "event": "card_moved",
                    "data": {
                        "slug": change.slug,
                        "new_group": new_group,
                        "group_label": new_group,
                        "before_slug": None,
                    },
                })

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

    def _slug_group_in_snapshot(self, slug: str) -> str | None:
        """Return the group name that holds `slug` in the in-memory snapshot,
        or None if not present."""
        for g in self._state.registry_groups:
            if slug in g.get("analyses", []):
                return g["name"]
        return None

    def _move_slug_in_snapshot(self, slug: str, new_group: str) -> None:
        """Mutate `_state.registry_groups` so `slug` lives in `new_group`,
        creating the group entry at the end if it doesn't yet exist."""
        # Remove from any existing group.
        for g in self._state.registry_groups:
            analyses = g.get("analyses", [])
            if slug in analyses:
                analyses.remove(slug)
        # Append to new group (creating it if needed).
        for g in self._state.registry_groups:
            if g["name"] == new_group:
                g["analyses"].append(slug)
                return
        self._state.registry_groups.append({
            "name": new_group,
            "analyses": [slug],
        })

    def _handle_agent_active(self, change: FileChange) -> None:
        if change.is_delete:
            self._state.agent_active = None
            self.bus.publish({"event": "agent_idle", "data": {}})
            return
        new_text = _safe_read_text(change.path) or ""
        edits_start_at = _parse_agent_active(new_text)
        self._state.agent_active = {"edits_start_at": edits_start_at}
        self.bus.publish({
            "event": "agent_active",
            "data": {"edits_start_at": edits_start_at},
        })

    def _handle_registry(self, change: FileChange) -> None:
        if change.is_delete:
            self._emit_full_reload("registry.yaml removed")
            return

        new_groups = _normalize_groups(load_registry(self.analyses_dir))

        # If a group rename is in flight, the on-disk registry's only
        # diff vs the snapshot should be the renamed group's name. The
        # snapshot was eagerly updated in `begin_rename`, so this diff
        # is normally empty. Either way, swallow events here — the
        # endpoint publishes a single `group_renamed` from
        # `end_rename_and_publish`.
        if self._state.pending_rename is not None:
            self._state.registry_groups = new_groups
            return

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

    def _prime_agent_active(self) -> None:
        """Read existing `.agent_active` marker (if any) into state. No event
        is published here — initial state is delivered through the `hello`
        event when a client connects."""
        marker = self.analyses_dir / ".agent_active"
        if not marker.exists():
            self._state.agent_active = None
            return
        text = _safe_read_text(marker) or ""
        self._state.agent_active = {"edits_start_at": _parse_agent_active(text)}

    def _refresh_wrapper_hashes(self) -> None:
        for child in self.analyses_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            wrapper = child / "analysis.py"
            if wrapper.is_file():
                text = _safe_read_text(wrapper)
                if text is not None:
                    self._state.content_hashes[wrapper] = _hash_text(text)
