"""Command-line interface for labdash."""

import argparse
import sys
from pathlib import Path

import yaml


def _load_config(start_dir: Path | None = None) -> dict:
    """Load collection.yaml from start_dir (or cwd).

    Each LabDash project is expected to either:
      - have a collection.yaml at the cwd (single-collection project), or
      - be invoked with `-c <collection_dir>` (multi-collection project).

    Returns a dict with at least a "_root" key pointing at the directory that
    will be used as the base for relative paths. May be an empty-ish stub
    ({"_root": cwd}) if no collection.yaml is found and -c will supply it.
    """
    search = start_dir or Path.cwd()

    collection_cfg = search / "collection.yaml"
    if collection_cfg.exists():
        with open(collection_cfg) as f:
            config = yaml.safe_load(f) or {}
        config["_root"] = search
        config.setdefault("analyses_dir", ".")
        return config

    # No collection.yaml in cwd. Valid only if -c is supplied.
    return {"_root": search}


# Keys merged from a collection.yaml into the running config when -c is used.
_COLLECTION_MERGE_KEYS = (
    "title",
    "port",
    "output_dir",
    "publication_format",
    "publication_dpi",
    "data_dir",
    "data_filter",
)


def _apply_collection_override(config: dict, collection: str | None):
    """Override analyses_dir if --collection was given, merging collection.yaml."""
    if not collection:
        return
    config["analyses_dir"] = collection
    collection_cfg = config["_root"] / collection / "collection.yaml"
    if collection_cfg.exists():
        with open(collection_cfg) as f:
            coll_config = yaml.safe_load(f) or {}
        for key in _COLLECTION_MERGE_KEYS:
            if key in coll_config:
                config[key] = coll_config[key]


def _resolve_dirs(config: dict) -> tuple[Path, Path]:
    """Return (analyses_dir, output_dir) from config.

    Rules for output_dir:
      1. If `output_dir` is explicitly set in collection.yaml, use it.
      2. Else if the analyses_dir contains a collection.yaml, default to
         _output/<analyses_dir.name>/ (per-collection subdir, prevents
         cross-collection output collisions).
      3. Else use _output/ (flat, for single-collection one-off projects).
    """
    root = config["_root"]
    if "analyses_dir" not in config:
        raise SystemExit(
            "labdash: no collection found. Either cd into a directory with "
            "collection.yaml or pass -c <collection_dir>."
        )
    analyses_dir = root / config["analyses_dir"]

    if "output_dir" in config:
        output_dir = root / config["output_dir"]
    elif (analyses_dir / "collection.yaml").exists():
        output_dir = root / "_output" / analyses_dir.name
    else:
        output_dir = root / "_output"
    return analyses_dir, output_dir


def cmd_build(args):
    """Build command: run analyses and generate static HTML."""
    from .runner import run_all
    from .builder import build_dashboard

    config = _load_config()
    _apply_collection_override(config, getattr(args, "collection", None))
    analyses_dir, output_dir = _resolve_dirs(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    slugs = args.slugs if args.slugs else None
    only_stale = getattr(args, "only_stale", False)
    print(f"Running analyses from {analyses_dir}...")
    results = run_all(analyses_dir, output_dir, slugs=slugs, only_stale=only_stale)

    success = sum(1 for r in results if r["success"])
    fail = sum(1 for r in results if not r["success"])
    print(f"\n{success} succeeded, {fail} failed")

    print("Building dashboard...")
    index_path = build_dashboard(analyses_dir, output_dir)
    print(f"Dashboard: {index_path}")


def cmd_serve(args):
    """Serve command: start live development server."""
    try:
        import uvicorn
    except ImportError:
        print("Error: install serve extras: pip install labdash[serve]")
        sys.exit(1)

    config = _load_config()
    _apply_collection_override(config, getattr(args, "collection", None))
    # Resolve dirs to surface missing-collection errors early (and to ensure
    # the server has analyses/output paths before it starts the ASGI app).
    _resolve_dirs(config)
    port = args.port or config.get("port", config.get("server_port", 8800))

    from .server import create_app
    app = create_app(config)
    print(f"LabDash server at http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_export(args):
    """Export command: gather publication-ready outputs."""
    from .export import export_figures, export_code

    config = _load_config()
    _apply_collection_override(config, getattr(args, "collection", None))
    analyses_dir, output_dir = _resolve_dirs(config)

    if args.subcommand == "figures":
        fmt = args.format or config.get("publication_format", "svg")
        dpi = args.dpi or config.get("publication_dpi", 300)
        dest = Path(args.output) if args.output else config["_root"] / "figures"
        export_figures(analyses_dir, output_dir, dest, fmt=fmt, dpi=dpi, status=args.status)
    elif args.subcommand == "code":
        dest = Path(args.output) if args.output else config["_root"] / "analysis_code"
        export_code(analyses_dir, dest, status=args.status)


def cmd_init(args):
    """Init command: scaffold a new analysis set."""
    target = Path(args.path) if args.path else Path.cwd()
    from .scaffold import init_project
    init_project(target)


def cmd_new_wrapper(args):
    """Scaffold a leaf wrapper analysis calling a shared _lib.plots module."""
    from .scaffold import new_wrapper

    config = _load_config()
    _apply_collection_override(config, args.collection)
    analyses_dir, _ = _resolve_dirs(config)
    deps = [s.strip() for s in (args.deps or "").split(",") if s.strip()]

    new_wrapper(
        analyses_dir=analyses_dir,
        slug=args.slug,
        plot_module=args.plot,
        upstream=args.upstream,
        upstream_file=args.upstream_file,
        deps=deps,
        title=args.title,
        group=args.group,
    )


def main():
    parser = argparse.ArgumentParser(prog="labdash", description="AI-agent-friendly analysis dashboard")
    sub = parser.add_subparsers(dest="command")

    collection_help = "Collection to operate on (path relative to project root, e.g. analysis/sim_task)"

    # build
    p_build = sub.add_parser("build", help="Run analyses and generate static HTML dashboard")
    p_build.add_argument("-c", "--collection", help=collection_help)
    p_build.add_argument("--only-stale", action="store_true",
                         help="Skip analyses whose outputs are already up-to-date")
    p_build.add_argument("slugs", nargs="*", help="Specific analysis slugs to run (default: all)")
    p_build.set_defaults(func=cmd_build)

    # serve
    p_serve = sub.add_parser("serve", help="Start live development server with in-browser editing")
    p_serve.add_argument("-c", "--collection", help=collection_help)
    p_serve.add_argument("--port", type=int, help="Server port (default: from config or 8800)")
    p_serve.set_defaults(func=cmd_serve)

    # export
    p_export = sub.add_parser("export", help="Export publication-ready figures or code")
    p_export.add_argument("-c", "--collection", help=collection_help)
    export_sub = p_export.add_subparsers(dest="subcommand")

    p_figs = export_sub.add_parser("figures", help="Export figures at publication quality")
    p_figs.add_argument("--format", choices=["svg", "pdf", "png"], help="Output format")
    p_figs.add_argument("--dpi", type=int, help="DPI for raster formats")
    p_figs.add_argument("--status", default="publication", help="Only export analyses with this status")
    p_figs.add_argument("--output", help="Output directory")

    p_code = export_sub.add_parser("code", help="Export self-contained code directory")
    p_code.add_argument("--status", default="publication", help="Only export analyses with this status")
    p_code.add_argument("--output", help="Output directory")

    p_export.set_defaults(func=cmd_export)

    # init
    p_init = sub.add_parser("init", help="Scaffold a new analysis set in a directory")
    p_init.add_argument("path", nargs="?", help="Target directory (default: current)")
    p_init.set_defaults(func=cmd_init)

    # new-wrapper
    p_wrap = sub.add_parser(
        "new-wrapper",
        help="Scaffold a leaf wrapper calling a shared _lib.plots module",
    )
    p_wrap.add_argument("slug", help="Wrapper directory name (becomes the analysis slug)")
    p_wrap.add_argument("-c", "--collection", required=True, help=collection_help)
    p_wrap.add_argument("--plot", required=True,
                        help="Shared plot module, e.g. _lib.plots.sde_accuracy")
    p_wrap.add_argument("--upstream",
                        help="Slug whose artifact is loaded as the input DataFrame")
    p_wrap.add_argument("--upstream-file", default="trials.parquet",
                        help="Filename in upstream/ to load (default: trials.parquet)")
    p_wrap.add_argument("--deps",
                        help="Comma-separated dependency slugs for meta.yaml")
    p_wrap.add_argument("--title", help="Human-readable title for meta.yaml")
    p_wrap.add_argument("--group", default="Uncategorized", help="Group name for meta.yaml")
    p_wrap.set_defaults(func=cmd_new_wrapper)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
