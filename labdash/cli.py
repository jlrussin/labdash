"""Command-line interface for labdash."""

import argparse
import sys
from pathlib import Path

import yaml


def _load_config(start_dir: Path | None = None) -> dict:
    """Find and load labdash.yaml from start_dir or cwd, walking up."""
    search = start_dir or Path.cwd()
    for d in [search, *search.parents]:
        cfg_path = d / "labdash.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                config = yaml.safe_load(f) or {}
            config["_root"] = d
            return config
    return {"_root": Path.cwd()}


def _resolve_dirs(config: dict) -> tuple[Path, Path]:
    """Return (analyses_dir, output_dir) from config."""
    root = config["_root"]
    analyses_dir = root / config.get("analyses_dir", "analyses")
    output_dir = root / config.get("output_dir", "_output")
    return analyses_dir, output_dir


def cmd_build(args):
    """Build command: run analyses and generate static HTML."""
    from .runner import run_all
    from .builder import build_dashboard

    config = _load_config()
    analyses_dir, output_dir = _resolve_dirs(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    slugs = args.slugs if args.slugs else None
    print(f"Running analyses from {analyses_dir}...")
    results = run_all(analyses_dir, output_dir, slugs=slugs)

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
    port = args.port or config.get("server_port", 8800)

    from .server import create_app
    app = create_app(config)
    print(f"LabDash server at http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_export(args):
    """Export command: gather publication-ready outputs."""
    from .export import export_figures, export_code

    config = _load_config()
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


def main():
    parser = argparse.ArgumentParser(prog="labdash", description="AI-agent-friendly analysis dashboard")
    sub = parser.add_subparsers(dest="command")

    # build
    p_build = sub.add_parser("build", help="Run analyses and generate static HTML dashboard")
    p_build.add_argument("slugs", nargs="*", help="Specific analysis slugs to run (default: all)")
    p_build.set_defaults(func=cmd_build)

    # serve
    p_serve = sub.add_parser("serve", help="Start live development server with in-browser editing")
    p_serve.add_argument("--port", type=int, help="Server port (default: from config or 8765)")
    p_serve.set_defaults(func=cmd_serve)

    # export
    p_export = sub.add_parser("export", help="Export publication-ready figures or code")
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
