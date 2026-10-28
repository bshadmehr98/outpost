import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from outpost.http import Http, HttpError


@pytest.fixture
def busy_server():
    """A server that answers 503 to the first request of each method, then 200 with what it was sent."""
    seen: list[tuple[str, int, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append((self.command, len(body), self.headers.get("Content-Length")))
            first = sum(1 for method, _, _ in seen if method == self.command) == 1
            status = 503 if first else 200
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = do_PUT = do_POST = do_HEAD = _answer  # noqa: N815  the names http.server calls

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()


@pytest.fixture
def no_waiting(monkeypatch):
    monkeypatch.setattr(Http, "_sleep", staticmethod(lambda s: None))


def test_reads_are_retried_on_a_busy_server(busy_server, no_waiting, capsys):
    url, seen = busy_server
    assert Http(url).get("/x", ok=(200,)).status == 200
    assert [method for method, _, _ in seen] == ["GET", "GET"]
    assert "HTTP 503 from" in capsys.readouterr().err  # the retry is said on stderr


@pytest.mark.parametrize("method", ["PUT", "POST"])
def test_writes_are_not_retried_because_the_first_one_may_have_landed(busy_server, no_waiting, method):
    url, seen = busy_server
    with pytest.raises(HttpError, match="HTTP 503"):
        Http(url).request(method, "/upload", data=b"package", ok=(200, 201))
    assert [m for m, _, _ in seen] == [method]


def test_a_file_body_is_streamed_with_its_length(busy_server, tmp_path):
    url, seen = busy_server
    blob = tmp_path / "layer.tar.gz"
    blob.write_bytes(b"x" * 3_000_000)
    response = Http(url).request("PUT", "/blob", data=blob)
    assert response.status == 503 and seen == [("PUT", 3_000_000, "3000000")]
    assert Http(url).request("PUT", "/blob", data=blob).body == blob.read_bytes()
