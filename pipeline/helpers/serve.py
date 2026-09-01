#!/usr/bin/env python3
"""Named serve shortcuts — the `npm run` equivalent for this Python repo.

Usage:
    python3 -m pipeline.helpers.serve [target] [--port PORT] [--host HOST]

Targets (like `npm run <script>`):
    atlas       Serve the interactive system atlas (default)
    dashboard   Serve the job-scraper triage.html dashboard
    root        Serve the job-hunter workspace root
    scraper     Serve the job-scraper repo root

Defaults: host 127.0.0.1, port 8765. Override with --port/--host.
Ctrl+C to stop. Opens the target file in your browser when possible.

Examples:
    python3 -m pipeline.helpers.serve                  # → atlas on :8765
    python3 -m pipeline.helpers.serve dashboard        # → triage.html on :8765
    python3 -m pipeline.helpers.serve atlas --port 8000
"""
from __future__ import annotations

import argparse
import os
import socketserver
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_ROOT = Path(os.environ.get("JOB_SCRAPER_DIR", str(REPO_ROOT.parent / "job-scraper")))

# target → (directory to serve, file to open in browser, optional description)
TARGETS: dict[str, tuple[Path, str, str]] = {
    "atlas": (
        REPO_ROOT / "docs",
        "atlas.html",
        "Interactive system atlas",
    ),
    "dashboard": (
        SCRAPER_ROOT,
        "triage.html",
        "Job-scraper triage dashboard",
    ),
    "root": (
        REPO_ROOT,
        "",
        "Job-hunter workspace root (directory listing)",
    ),
    "scraper": (
        SCRAPER_ROOT,
        "",
        "Job-scraper repo root (directory listing)",
    ),
}


def _resolve_target(name: str) -> tuple[Path, str, str]:
    if name not in TARGETS:
        available = ", ".join(sorted(TARGETS))
        print(f"Unknown target: {name!r}. Available: {available}", file=sys.stderr)
        sys.exit(2)
    directory, open_file, desc = TARGETS[name]
    if not directory.is_dir():
        print(f"Target directory does not exist: {directory}", file=sys.stderr)
        sys.exit(1)
    return directory, open_file, desc


def _serve(directory: Path, host: str, port: int, open_file: str, desc: str) -> None:
    os.chdir(directory)

    # Allow immediate socket reuse after Ctrl+C so re-running isn't blocked.
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter logs
            sys.stderr.write(f"  {self.address_string()} - {fmt % args}\n")

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer((host, port), Handler)

    url = f"http://{host}:{port}/{open_file}".rstrip("/")
    print(f"Serving {desc}")
    print(f"  dir:   {directory}")
    print(f"  url:   {url}")
    print(f"  (Ctrl+C to stop)")

    # Open the browser after a tiny delay so the server is listening first.
    if open_file:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Named serve shortcuts (npm-run style).",
        usage="python3 -m pipeline.helpers.serve [target] [--port PORT] [--host HOST]",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="atlas",
        choices=sorted(TARGETS),
        help="What to serve (default: atlas).",
    )
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--force-insecure",
        action="store_true",
        help="Required to bind to a non-localhost host. The workspace "
        "contains sensitive profile data — binding to 0.0.0.0 or a "
        "network IP exposes it without authentication.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open a browser window.",
    )
    args = parser.parse_args()

    # Security gate: require --force-insecure for non-localhost hosts.
    # The workspace contains _config/profile/ with PII — exposing it on the network
    # without auth is a data leak waiting to happen.
    localhost_hosts = {"127.0.0.1", "localhost", "::1"}
    if args.host not in localhost_hosts and not args.force_insecure:
        print(
            f"  ⛔  Refusing to bind to {args.host} — non-localhost host.\n"
            f"      The workspace contains sensitive profile data (_config/profile/)\n"
            f"      with no authentication. Binding to a network-accessible\n"
            f"      host would expose it to anyone on the network.\n"
            f"\n"
            f"      If you understand the risk, pass --force-insecure to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.host not in localhost_hosts and args.force_insecure:
        print(
            f"  ⚠️  WARNING: Binding to {args.host}. The _config/profile/ directory\n"
            f"      contains PII and will be accessible without authentication\n"
            f"      to anyone who can reach this host.",
            file=sys.stderr,
        )

    directory, open_file, desc = _resolve_target(args.target)
    if args.no_browser:
        open_file = ""
    _serve(directory, args.host, args.port, open_file, desc)


if __name__ == "__main__":
    main()
