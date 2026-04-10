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

from .runner import discover_analyses, run_analysis
from .builder import build_dashboard


class CodeUpdate(BaseModel):
    code: str


class MetaUpdate(BaseModel):
    group: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    order: int | None = None
    description: str | None = None
    methodology: str | None = None


class ConfigUpdate(BaseModel):
    title: str | None = None




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


def _update_registry(analyses_dir: Path):
    """Regenerate registry.yaml with current groups and tags."""
    from .builder import _update_registry as _builder_update_registry
    _builder_update_registry(analyses_dir)


def create_app(config: dict) -> FastAPI:
    """Create the FastAPI application."""
    root = config["_root"]
    analyses_dir = root / config.get("analyses_dir", "analyses")
    output_dir = root / config.get("output_dir", "_output")
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

    @app.post("/api/run/{slug}")
    def run_analysis_endpoint(slug: str):
        """Execute an analysis in-process and return the result."""
        analyses = discover_analyses(analyses_dir)
        match = [a for a in analyses if a["slug"] == slug]
        if not match:
            raise HTTPException(404, f"Analysis '{slug}' not found")
        analysis = match[0]

        # Run in-process (importlib) for data caching benefits
        result = run_analysis(analysis, output_dir)

        slug_output = output_dir / slug
        response = {
            "slug": slug,
            "success": result["success"],
            "stdout": "",
            "stderr": result.get("error", ""),
        }

        if result["success"]:
            for ext in [".png", ".svg", ".jpg"]:
                img_path = slug_output / f"output{ext}"
                if img_path.exists():
                    data = base64.b64encode(img_path.read_bytes()).decode()
                    mime = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg"}
                    response["image"] = f"data:{mime.get(ext[1:], 'image/png')};base64,{data}"
                    break

            stats_path = slug_output / "stats.json"
            if stats_path.exists():
                response["stats"] = json.loads(stats_path.read_text())

            table_path = slug_output / "output.html"
            if table_path.exists():
                response["table_html"] = table_path.read_text()

        return response

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
            _update_registry(analyses_dir)

        return {"status": "saved", "slug": slug, "updated": updated}

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
/* ── Live Mode: Show live-only buttons ──────────────── */
(function() {
    const reloadBtn = document.getElementById('reloadDataBtn');
    if (reloadBtn) reloadBtn.style.display = '';
    const runAllBtn = document.getElementById('runAllBtn');
    if (runAllBtn) runAllBtn.style.display = '';
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

/* ── Live Mode: Unified Monaco Editor ──────────────────── */
(function() {
    require.config({
        paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' }
    });

    const editors = {};
    const savedCode = {};  // last-saved version per slug

    require(['vs/editor/editor.main'], function() {
        // Replace all Pygments code blocks with Monaco editors (read-only)
        document.querySelectorAll('.card').forEach(card => {
            const slug = card.dataset.slug;
            const codeSection = card.querySelector('.expandable[id^="code-"]');
            if (!codeSection) return;

            const body = codeSection.querySelector('.expandable-body');
            const codeBlock = body.querySelector('.code-container');
            const errorEl = card.querySelector('.error-display');
            const outputDiv = card.querySelector('.card-output');

            // Hide the Pygments code block
            codeBlock.style.display = 'none';

            // Create Monaco container
            const monacoContainer = document.createElement('div');
            monacoContainer.style.cssText = 'height:400px;border:1px solid #333;border-radius:6px;overflow:hidden;';
            body.insertBefore(monacoContainer, codeBlock);

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
            body.insertBefore(btnBar, monacoContainer);

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
</script>'''
