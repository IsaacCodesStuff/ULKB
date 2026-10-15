import http.server
import threading
from io import StringIO

import pytest
from rich.console import Console

from builder.download import DownloadError, download_file

DATA = bytes(range(256)) * 4096  # 1 MiB


def _serve(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/f.bin"


def _console():
    return Console(file=StringIO(), force_terminal=False, width=100)


class Plain(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        self.wfile.write(DATA)


def make_flaky():
    """Drops the connection after 300 KB on the first request; honours Range afterwards."""
    state = {"n": 0}

    class Flaky(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            state["n"] += 1
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].rstrip("-")) if rng else 0
            body = DATA[start:]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(body)))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{len(DATA)-1}/{len(DATA)}")
            self.end_headers()
            if state["n"] == 1:
                self.wfile.write(body[:300_000])
                self.wfile.flush()
                self.connection.close()
            else:
                self.wfile.write(body)
    return Flaky, state


def test_plain_download(tmp_path):
    srv, url = _serve(Plain)
    out = download_file(url, tmp_path / "f.bin", _console())
    srv.shutdown()
    assert out.read_bytes() == DATA
    assert not (tmp_path / "f.bin.part").exists()


def test_resume_after_dropped_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("builder.download.time.sleep", lambda s: None)
    handler, state = make_flaky()
    srv, url = _serve(handler)
    out = download_file(url, tmp_path / "f.bin", _console(), timeout=5)
    srv.shutdown()
    assert out.read_bytes() == DATA
    assert state["n"] == 2


def test_404_fails_fast(tmp_path):
    class NotFound(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_error(404)
    srv, url = _serve(NotFound)
    with pytest.raises(DownloadError, match="404"):
        download_file(url, tmp_path / "f.bin", _console())
    srv.shutdown()
