"""FastAPI local development server with in-browser editing."""

import base64
import difflib
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError:
    raise ImportError("Install serve extras: pip install labdash[serve]")

from .runner import (
    discover_analyses,
    run_analysis,
    load_registry,
    _is_new_format_registry,
    expand_with_stale_upstream,
    stale_only,
)
from .builder import build_dashboard


class CodeUpdate(BaseModel):
    code: str


class LibCodeUpdate(BaseModel):
    path: str  # relative to _lib/, e.g. "plots/sde_accuracy.py"
    code: str


class MetaUpdate(BaseModel):
    group: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    description: str | None = None
    methodology: str | None = None


class ConfigUpdate(BaseModel):
    title: str | None = None


class GroupOrder(BaseModel):
    name: str
    analyses: list[str]


class RegistryOrderUpdate(BaseModel):
    groups: list[GroupOrder]


class AgentNotesUpdate(BaseModel):
    agent_notes: str




def _append_change_log(analyses_dir: Path, slug: str, change_type: str, details: str):
    """Append an entry to the collection-level change_log.md."""
    log_path = analyses_dir / "change_log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{timestamp}] `{slug}` — {change_type}\n\n{details}\n"

    if not log_path.exists():
        log_path.write_text("# Change Log\n\nUser edits made via the LabDash viewer. Agents should read this to learn user preferences.\n")

    with open(log_path, "a") as f:
        f.write(entry)


def _make_code_diff_summary(old_code: str, new_code: str) -> str:
    """Generate a concise unified diff between old and new code."""
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=1))
    if not diff:
        return "(no changes)"
    # Skip the --- / +++ headers, keep the hunks
    return "```diff\n" + "\n".join(diff[2:]) + "\n```"


def _sync_registry(analyses_dir: Path):
    """Sync registry.yaml with current analyses on disk."""
    from .builder import _sync_registry as _builder_sync_registry
    _builder_sync_registry(analyses_dir)


def _move_slug_in_registry(analyses_dir: Path, slug: str, old_group: str | None, new_group: str):
    """Move a slug between groups in registry.yaml. Creates new group if needed."""
    registry = load_registry(analyses_dir)
    if registry is None or not _is_new_format_registry(registry):
        _sync_registry(analyses_dir)
        return

    groups = registry.get("groups", [])

    # Remove from old group
    for group in groups:
        if slug in group.get("analyses", []):
            group["analyses"].remove(slug)
            break

    # Remove empty groups
    groups = [g for g in groups if g.get("analyses")]

    # Add to new group (at end of that group's list)
    target = None
    for group in groups:
        if group["name"] == new_group:
            target = group
            break
    if target:
        target["analyses"].append(slug)
    else:
        groups.append({"name": new_group, "analyses": [slug]})

    registry["groups"] = groups
    registry_path = analyses_dir / "registry.yaml"
    with open(registry_path, "w") as f:
        yaml.dump(registry, f, default_flow_style=False, sort_keys=False)


def create_app(config: dict) -> FastAPI:
    """Create the FastAPI application."""
    from .cli import _resolve_dirs
    analyses_dir, output_dir = _resolve_dirs(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="LabDash")

    # ── API endpoints ──────────────────────────────────────

    @app.get("/api/analyses")
    def list_analyses():
        """List all analyses with metadata."""
        analyses = discover_analyses(analyses_dir)
        result = []
        for a in analyses:
            meta = a["meta"]
            code = a["analysis_path"].read_text()
            result.append({
                "slug": a["slug"],
                "title": meta.get("title", a["slug"]),
                "group": meta.get("group", "Ungrouped"),
                "status": meta.get("status", "draft"),
                "tags": meta.get("tags", []),
                "code": code,
            })
        return result

    def _slug_payload(slug: str, result: dict) -> dict:
        """Build the viewer-facing response payload for one run result."""
        slug_output = output_dir / slug
        payload = {
            "slug": slug,
            "success": result["success"],
            "stderr": result.get("error", "") or "",
        }
        if not result["success"]:
            return payload
        for ext in [".png", ".svg", ".jpg"]:
            img_path = slug_output / f"output{ext}"
            if img_path.exists():
                data = base64.b64encode(img_path.read_bytes()).decode()
                mime = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg"}
                payload["image"] = f"data:{mime.get(ext[1:], 'image/png')};base64,{data}"
                break
        stats_path = slug_output / "stats.json"
        if stats_path.exists():
            payload["stats"] = json.loads(stats_path.read_text())
        table_path = slug_output / "output.html"
        if table_path.exists():
            payload["table_html"] = table_path.read_text()
        return payload

    @app.post("/api/run/{slug}")
    def run_analysis_endpoint(slug: str):
        """Run the target slug plus any transitively-stale upstream, in topo order.

        The target itself always runs (explicit click = explicit run). Upstream
        nodes run only if stale. Returns the target's payload plus a list of
        any additional slugs that ran so the viewer can refresh their cards.
        """
        analyses = discover_analyses(analyses_dir)
        if not any(a["slug"] == slug for a in analyses):
            raise HTTPException(404, f"Analysis '{slug}' not found")

        chain = expand_with_stale_upstream([slug], analyses, output_dir)

        upstream_payloads: list[dict] = []
        target_payload: dict | None = None
        for a in chain:
            result = run_analysis(a, output_dir)
            payload = _slug_payload(a["slug"], result)
            if a["slug"] == slug:
                target_payload = payload
            else:
                upstream_payloads.append(payload)
            if not result["success"]:
                # Abort the chain on first failure so downstream doesn't run
                # against stale/missing artifacts.
                break

        if target_payload is None:
            # Shouldn't happen since expand_with_stale_upstream always includes the target,
            # but guard against partial chain abort.
            target_payload = {"slug": slug, "success": False,
                              "stderr": "target did not run (upstream failure?)"}
        target_payload["upstream"] = upstream_payloads
        target_payload["stdout"] = ""
        return target_payload

    @app.post("/api/run-all-stale")
    def run_all_stale_endpoint():
        """Run every transitively-stale analysis in topo order."""
        analyses = discover_analyses(analyses_dir)
        chain = stale_only(analyses, output_dir)
        results = []
        for a in chain:
            result = run_analysis(a, output_dir)
            results.append(_slug_payload(a["slug"], result))
            if not result["success"]:
                break
        return {"ran": results, "total": len(chain)}

    @app.put("/api/code/{slug}")
    def save_code(slug: str, update: CodeUpdate):
        """Save edited code back to analysis.py."""
        analyses = discover_analyses(analyses_dir)
        match = [a for a in analyses if a["slug"] == slug]
        if not match:
            raise HTTPException(404, f"Analysis '{slug}' not found")
        analysis = match[0]

        # Log the diff before overwriting
        old_code = analysis["analysis_path"].read_text()
        if old_code != update.code:
            diff_summary = _make_code_diff_summary(old_code, update.code)
            _append_change_log(analyses_dir, slug, "code edit", diff_summary)

        analysis["analysis_path"].write_text(update.code)
        return {"status": "saved", "slug": slug}

    @app.get("/api/lib-raw")
    def list_lib_raw():
        """Return {rel_path: source_code} for every .py file under _lib/.

        Used by the live-mode client to populate Monaco editor buffers and
        the Copy button on shared-code panes.
        """
        from .builder import _resolve_lib_dir
        lib_dir = _resolve_lib_dir(analyses_dir)
        if lib_dir is None:
            return {}
        out: dict[str, str] = {}
        for f in sorted(lib_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            out[f.name] = f.read_text()
        plots_dir = lib_dir / "plots"
        if plots_dir.is_dir():
            for f in sorted(plots_dir.glob("*.py")):
                if f.name == "__init__.py":
                    continue
                out[f"plots/{f.name}"] = f.read_text()
        return out

    @app.put("/api/lib-code")
    def save_lib_code(update: LibCodeUpdate):
        """Save edited code back to a file under _lib/.

        Path-traversal-safe: the resolved target must stay inside _lib/.
        On success, clears _lib from sys.modules so the next run re-imports,
        returns the list of wrappers whose output is now stale because they
        TRANSITIVELY import this file, and the re-highlighted HTML so the
        client can refresh in-card shared panes without losing Pygments
        colors.
        """
        from .builder import _resolve_lib_dir, _highlight_python
        from .lib_graph import (
            LibImportError,
            parse_lib_imports,
            build_lib_graph,
            transitive_lib_closure,
        )
        lib_dir = _resolve_lib_dir(analyses_dir)
        if lib_dir is None:
            raise HTTPException(404, "_lib/ not found")

        # Validate path stays inside lib_dir (rejects .., absolute paths, etc.)
        lib_root = lib_dir.resolve()
        try:
            target = (lib_dir / update.path).resolve()
            target.relative_to(lib_root)
        except (ValueError, OSError):
            raise HTTPException(400, f"Invalid path: {update.path}")
        if not target.is_file():
            raise HTTPException(404, f"File not found: {update.path}")
        if target.suffix != ".py":
            raise HTTPException(400, "Only .py files are editable")

        # Log the diff before overwriting
        old_code = target.read_text()
        if old_code == update.code:
            return {
                "status": "unchanged",
                "path": update.path,
                "stale_slugs": [],
                "code_highlighted": _highlight_python(update.code),
            }

        diff_summary = _make_code_diff_summary(old_code, update.code)
        _append_change_log(
            analyses_dir, f"_lib/{update.path}", "shared code edit", diff_summary
        )
        target.write_text(update.code)

        # Clear _lib from sys.modules so next run re-imports fresh code.
        for k in [k for k in sys.modules if "_lib" in k]:
            del sys.modules[k]

        # Find wrappers that TRANSITIVELY import this file; they are now stale.
        # Build the import graph fresh (reflects the just-saved file). If the
        # new code introduces a non-static import anywhere in `_lib/`, surface
        # the error to the client — the edit is already saved on disk, so
        # refusing to compute stale slugs is the right tradeoff over rejecting
        # the save.
        rel = update.path.replace("\\", "/")
        stale_slugs: list[str] = []
        lib_graph_error: str | None = None
        try:
            graph = build_lib_graph(lib_dir)
            for a in discover_analyses(analyses_dir):
                direct = parse_lib_imports(a["analysis_path"], lib_dir=lib_dir)
                if rel in transitive_lib_closure(direct, graph):
                    stale_slugs.append(a["slug"])
        except LibImportError as e:
            lib_graph_error = str(e)

        response: dict = {
            "status": "saved",
            "path": update.path,
            "stale_slugs": stale_slugs,
            "code_highlighted": _highlight_python(update.code),
        }
        if lib_graph_error:
            response["lib_graph_error"] = lib_graph_error
        return response

    @app.get("/api/notes/{slug}")
    def get_notes(slug: str):
        """Get notes for an analysis."""
        analyses = discover_analyses(analyses_dir)
        match = [a for a in analyses if a["slug"] == slug]
        if not match:
            raise HTTPException(404, f"Analysis '{slug}' not found")
        notes_path = match[0]["dir"] / "notes.md"
        content = notes_path.read_text() if notes_path.exists() else ""
        return {"slug": slug, "notes": content}

    @app.put("/api/notes/{slug}")
    def save_notes(slug: str, update: CodeUpdate):
        """Save edited notes back to notes.md."""
        analyses = discover_analyses(analyses_dir)
        match = [a for a in analyses if a["slug"] == slug]
        if not match:
            raise HTTPException(404, f"Analysis '{slug}' not found")
        notes_path = match[0]["dir"] / "notes.md"
        old_notes = notes_path.read_text() if notes_path.exists() else ""
        notes_path.write_text(update.code)

        if old_notes != update.code:
            _append_change_log(analyses_dir, slug, "notes edit",
                               _make_code_diff_summary(old_notes, update.code))

        return {"status": "saved", "slug": slug}

    @app.patch("/api/meta/{slug}")
    def update_meta(slug: str, update: MetaUpdate):
        """Update fields in an analysis's meta.yaml (group, tags, etc.)."""
        analyses = discover_analyses(analyses_dir)
        match = [a for a in analyses if a["slug"] == slug]
        if not match:
            raise HTTPException(404, f"Analysis '{slug}' not found")
        meta_path = match[0]["dir"] / "meta.yaml"
        if not meta_path.exists():
            raise HTTPException(404, f"meta.yaml not found for '{slug}'")

        with open(meta_path) as f:
            meta = yaml.safe_load(f)

        updated = []
        changes = []
        old_group = meta.get("group")
        for key, value in update.model_dump(exclude_none=True).items():
            old_val = meta.get(key)
            if old_val != value:
                changes.append(f"- `{key}`: `{old_val}` → `{value}`")
            meta[key] = value
            updated.append(key)

        with open(meta_path, "w") as f:
            yaml.dump(meta, f, default_flow_style=False, sort_keys=False)

        if changes:
            _append_change_log(analyses_dir, slug, "metadata edit", "\n".join(changes))

        # If group changed, update registry (move slug between groups)
        new_group = update.group
        if new_group is not None and new_group != old_group:
            _move_slug_in_registry(analyses_dir, slug, old_group, new_group)
        elif changes:
            # Tags or other metadata changed — sync registry for tags
            _sync_registry(analyses_dir)

        return {"status": "saved", "slug": slug, "updated": updated}

    # ── Registry endpoints ────────────────────────────────

    @app.get("/api/registry")
    def get_registry():
        """Return current registry data."""
        registry = load_registry(analyses_dir)
        if registry is None:
            return {"groups": [], "tags": [], "agent_notes": ""}
        return {
            "groups": registry.get("groups", []),
            "tags": registry.get("tags", []),
            "agent_notes": registry.get("agent_notes", ""),
        }

    @app.put("/api/registry/order")
    def update_registry_order(update: RegistryOrderUpdate):
        """Save complete ordering state (group order + within-group order).

        Also updates meta.yaml group field for any analysis whose group changed.
        """
        registry_path = analyses_dir / "registry.yaml"
        old_registry = load_registry(analyses_dir) or {}

        # Build old group mapping: slug -> group name
        old_group_for = {}
        if _is_new_format_registry(old_registry):
            for group in old_registry.get("groups", []):
                for s in group.get("analyses", []):
                    old_group_for[s] = group["name"]

        # Build new group mapping and detect group changes
        group_changes = []
        for group in update.groups:
            for s in group.analyses:
                old_g = old_group_for.get(s)
                if old_g is not None and old_g != group.name:
                    group_changes.append((s, old_g, group.name))

        # Update meta.yaml group field for any changed analyses
        if group_changes:
            analyses = discover_analyses(analyses_dir)
            by_slug = {a["slug"]: a for a in analyses}
            for s, old_g, new_g in group_changes:
                if s in by_slug:
                    meta_path = by_slug[s]["dir"] / "meta.yaml"
                    with open(meta_path) as f:
                        meta = yaml.safe_load(f)
                    meta["group"] = new_g
                    with open(meta_path, "w") as f:
                        yaml.dump(meta, f, default_flow_style=False, sort_keys=False)
                    _append_change_log(analyses_dir, s, "group change (drag)",
                                       f"- `group`: `{old_g}` → `{new_g}`")

        # Write registry with new ordering
        new_registry = {
            "groups": [{"name": g.name, "analyses": g.analyses} for g in update.groups],
            "tags": old_registry.get("tags", []),
        }
        agent_notes = old_registry.get("agent_notes", "")
        if agent_notes:
            new_registry["agent_notes"] = agent_notes

        # Re-collect tags from meta.yaml files
        analyses = discover_analyses(analyses_dir)
        all_tags = set()
        for a in analyses:
            for t in a["meta"].get("tags", []):
                all_tags.add(t)
        new_registry["tags"] = sorted(all_tags)

        with open(registry_path, "w") as f:
            yaml.dump(new_registry, f, default_flow_style=False, sort_keys=False)

        return {"status": "saved", "group_changes": len(group_changes)}

    @app.patch("/api/registry/agent-notes")
    def update_agent_notes(update: AgentNotesUpdate):
        """Update agent_notes field in registry.yaml."""
        registry_path = analyses_dir / "registry.yaml"
        registry = load_registry(analyses_dir) or {}

        if not _is_new_format_registry(registry):
            raise HTTPException(400, "Registry must be in new format first (run labdash build)")

        registry["agent_notes"] = update.agent_notes

        with open(registry_path, "w") as f:
            yaml.dump(registry, f, default_flow_style=False, sort_keys=False)

        return {"status": "saved"}

    @app.patch("/api/config")
    def update_config(update: ConfigUpdate):
        """Update collection-level config (collection.yaml)."""
        # Always write to collection.yaml so each collection has its own config
        config_path = analyses_dir / "collection.yaml"

        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}

        for key, value in update.model_dump(exclude_none=True).items():
            cfg[key] = value

        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

        return {"status": "saved", "path": str(config_path)}

    @app.post("/api/reload-data")
    def reload_data():
        """Clear data cache by removing _lib modules from sys.modules."""
        to_remove = [k for k in sys.modules if '_lib' in k]
        for k in to_remove:
            del sys.modules[k]
        return {"status": "reloaded", "cleared": len(to_remove)}

    # ── Serve the dashboard ────────────────────────────────

    @app.get("/")
    def serve_dashboard():
        """Serve the dashboard with live mode enabled."""
        # Rebuild dashboard on each request (fast enough for dev)
        index_path = build_dashboard(analyses_dir, output_dir)
        html = index_path.read_text()

        # Inject live mode script before </body>
        live_script = _get_live_mode_script()
        html = html.replace("</body>", f"{live_script}\n</body>")

        return HTMLResponse(html)

    return app


def _get_live_mode_script() -> str:
    """Return JS that enables live edit/run/save with Monaco editor."""
    return '''
<!-- Monaco Editor loader from CDN -->
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js"></script>
<script>
/* ── Live Mode: Show live-only buttons, enable live ordering ── */
(function() {
    isLiveMode = true;
    const reloadBtn = document.getElementById('reloadDataBtn');
    if (reloadBtn) reloadBtn.style.display = '';
    const runAllBtn = document.getElementById('runAllBtn');
    if (runAllBtn) runAllBtn.style.display = '';
    const runAllStaleBtn = document.getElementById('runAllStaleBtn');
    if (runAllStaleBtn) runAllStaleBtn.style.display = '';
    // Hide reset order button in live mode (order is persisted to registry)
    const resetBtn = document.getElementById('resetOrderBtn');
    if (resetBtn) resetBtn.style.display = 'none';
    // Clear stale localStorage order (live mode uses registry order baked into HTML)
    try { localStorage.removeItem('labdash-card-order'); } catch(e) {}
})();

window.reloadData = async function() {
    const btn = document.getElementById('reloadDataBtn');
    btn.textContent = 'Reloading...';
    btn.disabled = true;
    try {
        const resp = await fetch('/api/reload-data', { method: 'POST' });
        if (resp.ok) {
            btn.textContent = 'Reloaded!';
            document.querySelectorAll('.card').forEach(card => {
                if (!card.querySelector('.badge-stale')) {
                    const badges = card.querySelector('.card-badges');
                    const stale = document.createElement('span');
                    stale.className = 'badge badge-stale';
                    stale.textContent = 'stale';
                    badges.appendChild(stale);
                }
            });
        }
    } catch(e) {
        btn.textContent = 'Error!';
    }
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Reload Data'; }, 2000);
};

/* ── Live Mode: Sidebar group dragging ────────────────── */
(function() {
    let draggedGroupLi = null;
    const groupNav = document.getElementById('groupNav');
    const groupItems = groupNav.querySelectorAll('li[data-group]');

    groupItems.forEach(li => {
        if (li.dataset.group === 'all') return; // "All groups" not draggable

        li.setAttribute('draggable', 'true');
        li.style.cursor = 'grab';

        li.addEventListener('dragstart', e => {
            draggedGroupLi = li;
            li.style.opacity = '0.4';
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', li.dataset.group);
        });

        li.addEventListener('dragend', () => {
            li.style.opacity = '';
            groupItems.forEach(l => l.classList.remove('drag-over'));
            draggedGroupLi = null;
        });

        li.addEventListener('dragover', e => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (draggedGroupLi && draggedGroupLi !== li && li.dataset.group !== 'all') {
                li.classList.add('drag-over');
            }
        });

        li.addEventListener('dragleave', () => {
            li.classList.remove('drag-over');
        });

        li.addEventListener('drop', e => {
            e.preventDefault();
            li.classList.remove('drag-over');
            if (!draggedGroupLi || draggedGroupLi === li) return;

            // Reorder sidebar: insert dragged before drop target
            groupNav.insertBefore(draggedGroupLi, li);

            // Reorder main content: move subtitle+card blocks to match sidebar order
            reorderMainByGroups();
            saveCardOrder();
        });
    });

    function reorderMainByGroups() {
        const container = document.querySelector('.main');
        // Read new group order from sidebar
        const newOrder = [];
        groupNav.querySelectorAll('li[data-group]').forEach(li => {
            if (li.dataset.group !== 'all') newOrder.push(li.dataset.group);
        });

        // Collect subtitle+card blocks per group
        const blocksByGroup = {};
        let currentGroup = null;
        let currentBlock = [];
        for (const el of Array.from(container.children)) {
            if (el.classList.contains('group-subtitle')) {
                if (currentGroup && currentBlock.length) {
                    blocksByGroup[currentGroup] = currentBlock;
                }
                currentGroup = el.dataset.group;
                currentBlock = [el];
            } else if (el.classList.contains('card')) {
                currentBlock.push(el);
            }
        }
        if (currentGroup && currentBlock.length) {
            blocksByGroup[currentGroup] = currentBlock;
        }

        // Re-append in new order (after the header elements)
        newOrder.forEach(groupName => {
            const block = blocksByGroup[groupName];
            if (block) block.forEach(el => container.appendChild(el));
        });

        updateGroupSubtitles();
    }
})();

/* ── Live Mode: Unified Monaco Editor ──────────────────── */
(function() {
    require.config({
        paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' }
    });

    const editors = {};
    const savedCode = {};  // last-saved version per slug

    require(['vs/editor/editor.main'], function() {
        // Replace the wrapper pane's Pygments code block with Monaco (editable).
        // Shared panes (read-only) keep their Pygments <pre> — no Monaco there.
        document.querySelectorAll('.card').forEach(card => {
            const slug = card.dataset.slug;
            const codeSection = card.querySelector('.expandable[id^="code-"]');
            if (!codeSection) return;

            // The wrapper pane is the element we inject Monaco into. It was
            // a direct child of .expandable-body pre-tabs; post-tabs it's the
            // .code-pane[data-pane-kind="wrapper"] descendant. Either works.
            const wrapperPane = codeSection.querySelector('.code-pane[data-pane-kind="wrapper"]')
                             || codeSection.querySelector('.expandable-body');
            const codeBlock = wrapperPane.querySelector('.code-container');
            const errorEl = card.querySelector('.error-display');
            const outputDiv = card.querySelector('.card-output');

            // Hide the Pygments code block
            codeBlock.style.display = 'none';

            // Create Monaco container
            const monacoContainer = document.createElement('div');
            monacoContainer.style.cssText = 'height:400px;border:1px solid #333;border-radius:6px;overflow:hidden;';
            wrapperPane.insertBefore(monacoContainer, codeBlock);

            // Store initial code
            savedCode[slug] = rawCode[slug];

            // Create read-only Monaco editor
            const ed = monaco.editor.create(monacoContainer, {
                value: rawCode[slug],
                language: 'python',
                theme: 'vs-dark',
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 4,
                insertSpaces: true,
                wordWrap: 'on',
                padding: { top: 8 },
                readOnly: true,
                domReadOnly: true,
            });
            editors[slug] = ed;

            // Create button bar
            const btnBar = document.createElement('div');
            btnBar.style.cssText = 'display:flex;gap:0.5rem;padding:0.5rem 0;align-items:center;';

            const editBtn = document.createElement('button');
            editBtn.className = 'btn btn-edit';
            editBtn.textContent = 'Edit';

            const runBtn = document.createElement('button');
            runBtn.className = 'btn btn-run';
            runBtn.textContent = 'Run';

            const saveBtn = document.createElement('button');
            saveBtn.className = 'btn btn-save';
            saveBtn.textContent = 'Save';
            saveBtn.style.display = 'none';

            const saveRunBtn = document.createElement('button');
            saveRunBtn.className = 'btn btn-run';
            saveRunBtn.textContent = 'Save & Run';
            saveRunBtn.style.display = 'none';

            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'btn btn-cancel';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.style.display = 'none';

            btnBar.append(editBtn, runBtn, saveBtn, saveRunBtn, cancelBtn);
            wrapperPane.insertBefore(btnBar, monacoContainer);

            // ── Edit mode toggle ──
            function enterEditMode() {
                ed.updateOptions({ readOnly: false, domReadOnly: false });
                monacoContainer.style.borderColor = '#3498db';
                editBtn.style.display = 'none';
                runBtn.style.display = 'none';
                saveBtn.style.display = '';
                saveRunBtn.style.display = '';
                cancelBtn.style.display = '';
                codeSection.classList.add('open');
                ed.focus();
            }

            function exitEditMode() {
                ed.updateOptions({ readOnly: true, domReadOnly: true });
                monacoContainer.style.borderColor = '#333';
                editBtn.style.display = '';
                runBtn.style.display = '';
                saveBtn.style.display = 'none';
                saveRunBtn.style.display = 'none';
                cancelBtn.style.display = 'none';
            }

            editBtn.addEventListener('click', enterEditMode);

            cancelBtn.addEventListener('click', () => {
                // Revert to last saved version
                ed.setValue(savedCode[slug]);
                rawCode[slug] = savedCode[slug];
                exitEditMode();
            });

            // ── Save / Run ──
            function showSpinner() {
                const overlay = document.createElement('div');
                overlay.className = 'run-overlay';
                overlay.innerHTML = '<div class="spinner"></div>';
                outputDiv.appendChild(overlay);
                return overlay;
            }

            async function saveCode() {
                const code = ed.getValue();
                const resp = await fetch('/api/code/' + slug, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code: code}),
                });
                if (resp.ok) {
                    rawCode[slug] = code;
                    savedCode[slug] = code;
                    return true;
                }
                return false;
            }

            async function runAnalysis() {
                errorEl.style.display = 'none';
                const overlay = showSpinner();
                try {
                    const resp = await fetch('/api/run/' + slug, {method: 'POST'});
                    const data = await resp.json();
                    overlay.remove();
                    // Refresh upstream cards that were run as part of the chain.
                    if (data.upstream && Array.isArray(data.upstream)) {
                        for (const up of data.upstream) {
                            if (typeof applyRunPayload === 'function') {
                                applyRunPayload(up.slug, up);
                            }
                        }
                    }
                    if (data.success) {
                        if (data.image) {
                            outputDiv.innerHTML = '<img src="' + data.image + '" alt="' + slug + '">';
                        } else if (data.table_html) {
                            outputDiv.innerHTML = '<div class="card-output-table-wrapper">' + data.table_html + '<' + '/div>';
                        }
                        const staleBadge = card.querySelector('.badge-stale');
                        if (staleBadge) staleBadge.remove();
                        return true;
                    } else {
                        errorEl.textContent = data.stderr || 'Unknown error';
                        errorEl.style.display = 'block';
                        return false;
                    }
                } catch(e) {
                    overlay.remove();
                    errorEl.textContent = e.message;
                    errorEl.style.display = 'block';
                    return false;
                }
            }

            saveBtn.addEventListener('click', async () => {
                saveBtn.textContent = 'Saving...';
                saveBtn.disabled = true;
                const ok = await saveCode();
                saveBtn.textContent = ok ? 'Saved!' : 'Error!';
                saveBtn.disabled = false;
                setTimeout(() => { saveBtn.textContent = 'Save'; }, 1500);
            });

            saveRunBtn.addEventListener('click', async () => {
                saveRunBtn.textContent = 'Saving...';
                saveRunBtn.disabled = true;
                const saved = await saveCode();
                if (saved) {
                    saveRunBtn.textContent = 'Running...';
                    const ran = await runAnalysis();
                    saveRunBtn.textContent = ran ? 'Done!' : 'Failed';
                } else {
                    saveRunBtn.textContent = 'Save failed!';
                }
                saveRunBtn.disabled = false;
                setTimeout(() => { saveRunBtn.textContent = 'Save & Run'; }, 2000);
            });

            runBtn.addEventListener('click', async () => {
                runBtn.textContent = 'Running...';
                runBtn.disabled = true;
                const ok = await runAnalysis();
                runBtn.textContent = ok ? 'Done!' : 'Failed';
                runBtn.disabled = false;
                setTimeout(() => { runBtn.textContent = 'Run'; }, 2000);
            });
        });
    });
})();

/* ── Live Mode: Editable shared-code panel (Monaco) ────── */
(function() {
    // Fetch all _lib sources once; populates libRawCode for Copy + Monaco.
    fetch('/api/lib-raw').then(r => r.ok ? r.json() : {}).then(data => {
        Object.assign(libRawCode, data);
    }).catch(() => {});

    const panel = document.getElementById('libCodePanel');
    const body = panel.querySelector('.lib-code-body');
    const headerActions = document.getElementById('libCodeHeaderActions');
    let libEditor = null;

    function markStaleBadges(slugs) {
        for (const slug of slugs) {
            const card = document.getElementById('card-' + slug);
            if (!card) continue;
            if (!card.querySelector('.badge-stale')) {
                const badges = card.querySelector('.card-badges');
                const stale = document.createElement('span');
                stale.className = 'badge badge-stale';
                stale.textContent = 'stale';
                badges.appendChild(stale);
            }
        }
    }

    panel.addEventListener('libpanel:opened', e => {
        const name = e.detail.name;

        // Clear existing editor / buttons
        headerActions.innerHTML = '';
        if (libEditor) { libEditor.dispose(); libEditor = null; }
        body.innerHTML = '';

        // Create fresh Monaco container.
        const mc = document.createElement('div');
        mc.style.cssText = 'height:calc(100vh - 52px);border:none;';
        body.appendChild(mc);

        const source = libRawCode[name];
        if (source === undefined) {
            // Fall back to fetch if not preloaded yet.
            fetch('/api/lib-raw').then(r => r.json()).then(data => {
                Object.assign(libRawCode, data);
                if (panel.dataset.currentFile === name) {
                    mountEditor(mc, name, libRawCode[name] || '');
                }
            });
        } else {
            mountEditor(mc, name, source);
        }
    });

    panel.addEventListener('libpanel:closed', () => {
        if (libEditor) { libEditor.dispose(); libEditor = null; }
        headerActions.innerHTML = '';
    });

    function mountEditor(container, name, value) {
        require(['vs/editor/editor.main'], function() {
            const editor = monaco.editor.create(container, {
                value,
                language: 'python',
                theme: 'vs-dark',
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 4,
                insertSpaces: true,
                wordWrap: 'on',
                padding: { top: 8 },
                readOnly: false,
                domReadOnly: false,
            });
            libEditor = editor;

            const saveBtn = document.createElement('button');
            saveBtn.className = 'btn btn-save';
            saveBtn.textContent = 'Save';
            headerActions.appendChild(saveBtn);

            saveBtn.addEventListener('click', async () => {
                const code = editor.getValue();
                saveBtn.textContent = 'Saving...';
                saveBtn.disabled = true;
                try {
                    const resp = await fetch('/api/lib-code', {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ path: name, code })
                    });
                    const data = await resp.json();
                    if (resp.ok) {
                        libRawCode[name] = code;
                        // Refresh in-card shared panes with Pygments-highlighted HTML
                        // returned from the server so edits keep syntax colors.
                        if (data.code_highlighted !== undefined) {
                            libCode[name] = data.code_highlighted;
                            document.querySelectorAll('.code-pane[data-pane-kind="shared"][data-lib-name="' + CSS.escape(name) + '"] pre code').forEach(el => {
                                el.innerHTML = data.code_highlighted;
                            });
                        }
                        markStaleBadges(data.stale_slugs || []);
                        saveBtn.textContent = (data.stale_slugs && data.stale_slugs.length)
                            ? ('Saved · ' + data.stale_slugs.length + ' stale')
                            : 'Saved!';
                    } else {
                        saveBtn.textContent = 'Error: ' + (data.detail || resp.status);
                    }
                } catch(e) {
                    saveBtn.textContent = 'Error!';
                }
                saveBtn.disabled = false;
                setTimeout(() => { saveBtn.textContent = 'Save'; }, 2500);
            });
        });
    }
})();
</script>'''
