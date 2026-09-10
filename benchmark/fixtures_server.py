import functools
import http.server
import socketserver
import threading
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FixtureServer:
    """Local deterministic HTTP server serving the benchmark fixtures.

    Binding on 127.0.0.1 keeps Tier 1/2 verification fully controlled:
    no external site failures, stable pages, objective expected values.
    """

    def __init__(self, port=0):
        self._httpd = _ThreadingServer(("127.0.0.1", port), _FixtureHandler)
        self._thread = None

    @property
    def base_url(self):
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass

    def url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()