"""A small HTTP helper over urllib. Supports a private CA, a fixed Host header
(for talking to Gitea through an SSH tunnel), Basic and Bearer auth, and downloads."""

import base64
import hashlib
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# A dropped connection or a busy server is not a reason to fail a fetch of a thousand files.
# Reads are retried on network errors; every method is retried on these status codes.
RETRY_WAIT = (1, 3, 9)
RETRY_STATUS = {429, 500, 502, 503, 504}
NETWORK_ERRORS = (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, ssl.SSLError)


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        import json
        return json.loads(self.body or b"null")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status = status
        self.body = body


class Http:
    def __init__(self, base: str = "", *, ca_file: str | Path | None = None, verify_hostname: bool = True,
                 host_header: str | None = None, basic: tuple[str, str] | None = None, timeout: int = 120):
        self.base = base.rstrip("/")
        self.host_header = host_header
        self.timeout = timeout
        self.bearer: str | None = None
        self.basic = basic
        ctx = ssl.create_default_context()
        if ca_file:
            ctx.load_verify_locations(str(ca_file))
        if not verify_hostname:
            ctx.check_hostname = False  # the SSH tunnel already authenticated the server
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def _headers(self, extra: dict | None) -> dict:
        headers = {"User-Agent": "outpost"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        elif self.basic:
            token = base64.b64encode(f"{self.basic[0]}:{self.basic[1]}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        headers.update(extra or {})
        return headers

    def request(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None,
                ok: tuple[int, ...] | None = None) -> Response:
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(headers))
        if self.host_header:
            req.add_unredirected_header("Host", self.host_header)
        for attempt in range(len(RETRY_WAIT) + 1):
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    response = Response(resp.status, dict(resp.headers), resp.read())
            except urllib.error.HTTPError as e:
                response = Response(e.code, dict(e.headers), e.read())
            except NETWORK_ERRORS as e:
                if method not in ("GET", "HEAD") or attempt == len(RETRY_WAIT):
                    raise
                self._retry(attempt, url, str(e))
                continue
            if response.status in RETRY_STATUS and attempt < len(RETRY_WAIT):
                self._retry(attempt, url, f"HTTP {response.status}")
                continue
            break
        if ok is not None and response.status not in ok:
            raise HttpError(response.status, response.text, url)
        return response

    def _retry(self, attempt: int, url: str, why: str) -> None:
        wait = RETRY_WAIT[attempt]
        print(f"    {why} from {url.split('?')[0][:80]}; retrying in {wait}s ({attempt + 1}/{len(RETRY_WAIT)})", file=sys.stderr, flush=True)
        self._sleep(wait)

    _sleep = staticmethod(time.sleep)

    def get(self, path, **kw) -> Response:
        return self.request("GET", path, **kw)

    def download(self, path: str, dest: Path, headers: dict | None = None, sha256: str | None = None, quiet: bool = False) -> Path:
        """Stream a URL to a file. Verifies sha256 when given. Writes to dest.part first.
        A connection that drops half way is retried from the start, a few times."""
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, headers=self._headers(headers))
        if self.host_header:
            req.add_unredirected_header("Host", self.host_header)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        for attempt in range(len(RETRY_WAIT) + 1):
            digest = hashlib.sha256()
            try:
                with self._opener.open(req, timeout=self.timeout) as resp, open(part, "wb") as out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if total and not quiet and sys.stderr.isatty():
                            print(f"\r    {dest.name}: {done * 100 // total}%", end="", file=sys.stderr)
                    if total and not quiet and sys.stderr.isatty():
                        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
                break
            except urllib.error.HTTPError as e:
                part.unlink(missing_ok=True)
                if e.code in RETRY_STATUS and attempt < len(RETRY_WAIT):
                    self._retry(attempt, url, f"HTTP {e.code}")
                    continue
                raise HttpError(e.code, e.read().decode("utf-8", "replace"), url) from None
            except NETWORK_ERRORS as e:
                part.unlink(missing_ok=True)
                if attempt == len(RETRY_WAIT):
                    raise
                self._retry(attempt, url, str(e))
        if sha256 and digest.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            raise ValueError(f"{url}: sha256 mismatch (expected {sha256}, got {digest.hexdigest()})")
        part.replace(dest)
        return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
