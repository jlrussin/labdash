"""Filesystem watcher for `labdash serve` live-mode propagation.

Watches the collection's analysis directory for edits to:
  - `<slug>/analysis.py` wrappers
  - `<slug>/meta.yaml`
  - `registry.yaml`
  - any `*.py` under `_lib/` (which may live inside or beside the
    analyses_dir — `_resolve_lib_dir` handles the lookup)

Classifies events, debounces editor save-bursts (tempfile/rename
patterns), and delegates to a callback. The callback receives a
`FileChange` describing the kind of file that changed; semantic
translation into SSE events is the translator's job (see
`live_translator.py`).

Hidden files, swap-file suffixes, and non-`.py` files inside `_lib/`
are filtered out at the source.

The watcher runs in a watchdog `Observer` thread. Callbacks fire on the
debounce-timer thread, which the watcher manages internally.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


FileKind = Literal["wrapper", "meta", "registry", "lib", "structural"]


@dataclass(frozen=True)
class FileChange:
    """A coalesced filesystem event ready for translation.

    `path` is absolute; `kind` is one of the FileKind literals; `slug` is
    the slug directory name (for wrapper/meta) or `None`.
    `is_delete` is True when the path was removed.
    """

    path: Path
    kind: FileKind
    slug: str | None
    is_delete: bool


_IGNORE_BASENAME_PREFIXES = (".", "__pycache__")
_IGNORE_SUFFIXES = (".swp", ".swx", ".swo", ".tmp", "~")


def _should_ignore(path: Path) -> bool:
    name = path.name
    if any(name.startswith(p) for p in _IGNORE_BASENAME_PREFIXES):
        return True
    if any(name.endswith(s) for s in _IGNORE_SUFFIXES):
        return True
    if any(part == "__pycache__" for part in path.parts):
        return True
    return False


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class FileWatcher:
    """Watch a collection's analyses_dir (and its `_lib/`) for live edits.

    Usage:
        w = FileWatcher(analyses_dir, lib_dir=lib_dir, on_change=cb)
        w.start()
        ...
        w.stop()

    `lib_dir` may be inside `analyses_dir` (one observer covers both) or
    a sibling (a second observer covers the `_lib/` tree).
    """

    def __init__(
        self,
        analyses_dir: Path,
        on_change: Callable[[FileChange], None],
        *,
        lib_dir: Path | None = None,
        debounce_s: float = 0.2,
    ):
        self._root = analyses_dir.resolve()
        self._lib_dir = lib_dir.resolve() if lib_dir is not None else None
        self._on_change = on_change
        self._debounce_s = debounce_s
        self._observer: Observer | None = None
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._observer is not None:
            return
        handler = _DispatchHandler(self)
        obs = Observer()
        obs.schedule(handler, str(self._root), recursive=True)
        if self._lib_dir is not None and not _is_subpath(self._lib_dir, self._root):
            obs.schedule(handler, str(self._lib_dir), recursive=True)
        obs.start()
        self._observer = obs

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()

    # ── classification ─────────────────────────────────────────────────

    def classify(self, path: Path) -> FileChange | None:
        """Map an absolute path to a `FileChange`, or None if irrelevant."""
        if _should_ignore(path):
            return None
        try:
            abspath = path.resolve()
        except OSError:
            return None

        # `_lib/` first — handles both inside-root and sibling-root layouts.
        if self._lib_dir is not None and _is_subpath(abspath, self._lib_dir):
            if path.suffix == ".py":
                return FileChange(path=path, kind="lib", slug=None, is_delete=False)
            return None

        if not _is_subpath(abspath, self._root):
            return None
        rel = abspath.relative_to(self._root)
        parts = rel.parts
        if not parts:
            return None

        # registry.yaml at the collection root
        if len(parts) == 1 and parts[0] == "registry.yaml":
            return FileChange(path=path, kind="registry", slug=None, is_delete=False)

        # `_lib/` inside the analyses_dir (no separate observer needed)
        if parts[0] == "_lib":
            if path.suffix == ".py":
                return FileChange(path=path, kind="lib", slug=None, is_delete=False)
            return None

        # <slug>/analysis.py | <slug>/meta.yaml
        if len(parts) == 2:
            slug, fname = parts
            if slug.startswith("_"):
                return None
            if fname == "analysis.py":
                return FileChange(path=path, kind="wrapper", slug=slug, is_delete=False)
            if fname == "meta.yaml":
                return FileChange(path=path, kind="meta", slug=slug, is_delete=False)

        return None

    # ── internal dispatch (called from DispatchHandler) ────────────────

    def _enqueue(self, change: FileChange) -> None:
        """Debounce + deliver. Thread-safe."""
        key = change.path
        with self._lock:
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(self._debounce_s, self._fire, args=(change,))
            t.daemon = True
            self._timers[key] = t
            t.start()

    def _fire(self, change: FileChange) -> None:
        with self._lock:
            self._timers.pop(change.path, None)
        try:
            self._on_change(change)
        except Exception:
            import traceback
            traceback.print_exc()


class _DispatchHandler(FileSystemEventHandler):
    """Bridge between watchdog and FileWatcher.

    Watchdog emits one event object per inotify/FSEvents notification.
    We classify the path, attach `is_delete`, and hand off to the
    watcher's per-path debouncer.
    """

    def __init__(self, watcher: FileWatcher):
        self._w = watcher

    def _handle(self, src_path: str, is_delete: bool = False) -> None:
        change = self._w.classify(Path(src_path))
        if change is None:
            return
        if is_delete:
            change = FileChange(
                path=change.path,
                kind=change.kind,
                slug=change.slug,
                is_delete=True,
            )
        self._w._enqueue(change)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, is_delete=False)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, is_delete=False)

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, is_delete=True)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, is_delete=True)
        self._handle(event.dest_path, is_delete=False)
