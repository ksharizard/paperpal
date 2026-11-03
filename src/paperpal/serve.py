"""Serve the static PaperPal app and the source PDFs locally."""

from __future__ import annotations

import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
BLOCKED = {".git", ".venv", "__pycache__", "node_modules"}


class PaperPalHandler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        path = unquote(urlparse(self.path).path)
        if any(part in BLOCKED for part in Path(path).parts):
            self.send_error(404)
            return
        route_names = {"bank", "practice", "papers"}
        route = Path(path).name if path not in {"/", "/web", "/web/"} else ""
        if path in {"/", "/web", "/web/"}:
            self.path = "/web/index.html"
        elif path in {f"/{name}" for name in route_names} | {f"/web/{name}" for name in route_names}:
            self.path = f"/web/index.html?view={route}"
        super().do_GET()

    def list_directory(self, path):  # noqa: N802 - http.server API
        self.send_error(404, "Directory listing is disabled")
        return None

    def log_message(self, format, *args):  # noqa: A002 - http.server API
        if "/data/" in str(args[0] if args else ""):
            return
        super().log_message(format, *args)


def serve(port=8000, open_browser=True):
    handler = partial(PaperPalHandler, directory=str(ROOT))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        raise SystemExit(f"Could not bind port {port}: {error}") from error

    url = f"http://127.0.0.1:{port}/web/"
    print(f"PaperPal running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
