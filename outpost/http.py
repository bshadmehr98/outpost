"""A small HTTP client over urllib, for registries, mirrors and Gitea.

    http = Http("https://gitea.internal", ca_file="pki/ca.crt", basic=("outpost", token))
    http.get("/api/v1/version", ok=(200,)).json()      # HttpError on any other status
    http.request("PUT", "/api/packages/...", data=Path("box/files/x"))   # a file body is streamed from disk
    http.request("POST", "/api/packages/...", data=StreamedBody([b"head", Path("x.whl"), b"tail"]))  # so is this
    http.download("https://example.com/tool.tgz", Path("box/files/tool.tgz"), sha256="...")

It supports a private CA, a fixed Host header (for talking to Gitea through an ssh tunnel), Basic
and Bearer auth, and downloads that stream to disk.

A dropped connection or a busy server is not a reason to fail a fetch of a thousand files, so reads
(GET and HEAD) are retried a few times, and each retry is said on stderr. Writes are never retried:
a PUT or POST that failed with a 5xx may still have landed, and sending it again could upload twice.

Every request and download is logged at DEBUG on the `outpost.http` logger.
"""

import base64
import hashlib
import json
import logging
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RETRY_WAIT = (1, 3, 9)  # seconds before each retry
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRIED_METHODS = ("GET", "HEAD")  # reads only: a write that failed may still have landed
NETWORK_ERRORS = (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, ssl.SSLError)
CHUNK = 1 << 20  # 1 MB
BASE64_CHUNK = 3 * (1 << 18)  # 768 KB: a multiple of 3 bytes, so each chunk encodes without padding in the middle


@dataclass
class Response:
    """A response with its whole body read."""

    status: int
    headers: dict
    body: bytes

    def json(self):
        """The body as JSON; an empty body is None."""
        return json.loads(self.body or b"null")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass(frozen=True)
class Base64File:
    """A file in a StreamedBody that is sent base64-encoded (npm wants the tarball inside a JSON document)."""

    path: Path

    @property
    def size(self) -> int:
        """The length of the base64 text: 4 characters for every 3 bytes, the last group padded."""
        return 4 * -(-self.path.stat().st_size // 3)  # -(-a // b) rounds the division up

    def chunks(self) -> Iterator[bytes]:
        with open(self.path, "rb") as file:
            while chunk := file.read(BASE64_CHUNK):
                yield base64.b64encode(chunk)


class StreamedBody:
    """A request body sent piece by piece: byte strings and files, each file read in chunks as it is sent,
    so a big file is never held in memory whole.

        body = StreamedBody([b"--boundary ...", Path("box/pypi/x.whl"), b"--boundary--"])
        http.request("POST", url, data=body)     # Content-Length is body.size
    """

    def __init__(self, pieces: list[bytes | Path | Base64File]):
        self.pieces = pieces

    @property
    def size(self) -> int:
        """The number of bytes the body sends."""
        return sum(_piece_size(piece) for piece in self.pieces)

    def __iter__(self) -> Iterator[bytes]:
        """The body, chunk by chunk (urllib sends an iterable body this way)."""
        for piece in self.pieces:
            if isinstance(piece, bytes):
                yield piece
            elif isinstance(piece, Base64File):
                yield from piece.chunks()
            else:
                yield from _file_chunks(piece)


def _piece_size(piece: bytes | Path | Base64File) -> int:
    if isinstance(piece, bytes):
        return len(piece)
    if isinstance(piece, Base64File):
        return piece.size
    return piece.stat().st_size


def _file_chunks(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            yield chunk


class HttpError(Exception):
    """A response with a status the caller did not accept. `.status` and `.body` say what the server said."""

    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status = status
        self.body = body


class Http:
    """One HTTP endpoint: a base URL, how to trust it, and how to log in."""

    def __init__(
        self,
        base: str = "",
        *,
        ca_file: str | Path | None = None,
        verify_hostname: bool = True,
        host_header: str | None = None,
        basic: tuple[str, str] | None = None,
        timeout: int = 120,
    ):
        """
        Args:
            base: put in front of every path that is not a full URL.
            ca_file: a CA to trust, besides the system's.
            verify_hostname: False when an ssh tunnel already authenticated the server.
            host_header: a fixed Host header, for a server reached through a tunnel on 127.0.0.1.
            basic: (user, password) for Basic auth; a `bearer` token set later wins over it.
            timeout: seconds to wait for the server on each request.
        """
        self.base = base.rstrip("/")
        self.host_header = host_header
        self.timeout = timeout
        self.bearer: str | None = None
        self.basic = basic
        ctx = ssl.create_default_context()
        if ca_file:
            ctx.load_verify_locations(str(ca_file))
        if not verify_hostname:
            ctx.check_hostname = False  # the ssh tunnel already authenticated the server
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def get(self, path: str, **kw) -> Response:
        """A GET request; see request()."""
        return self.request("GET", path, **kw)

    def request(
        self,
        method: str,
        path: str,
        data: bytes | Path | StreamedBody | None = None,
        headers: dict | None = None,
        ok: tuple[int, ...] | None = None,
    ) -> Response:
        """Send one request and read the whole response. GET and HEAD are retried on network errors and 5xx.

        Args:
            path: a path below base, or a full URL.
            data: the body: bytes, or a file or a StreamedBody, which are streamed from disk.
            ok: the statuses to accept; any other raises HttpError. None accepts every status.

        Raises:
            HttpError: the status is not in ok.
            URLError, ConnectionError, TimeoutError, SSLError: the network failed (after the retries, for a read).
        """
        url = self._url(path)
        retried = method in RETRIED_METHODS
        for attempt in range(len(RETRY_WAIT) + 1):
            started = time.monotonic()
            try:
                response = self._send(method, url, data, headers)
            except NETWORK_ERRORS as e:
                logger.debug("%s %s: %s (%.1fs)", method, _no_query(url), e, time.monotonic() - started)
                if not retried or attempt == len(RETRY_WAIT):
                    raise
                self._retry(attempt, url, str(e))
                continue
            logger.debug(
                "%s %s: HTTP %d, %d bytes (%.1fs)",
                method,
                _no_query(url),
                response.status,
                len(response.body),
                time.monotonic() - started,
            )
            if retried and response.status in RETRY_STATUS and attempt < len(RETRY_WAIT):
                self._retry(attempt, url, f"HTTP {response.status}")
                continue
            break
        if ok is not None and response.status not in ok:
            raise HttpError(response.status, response.text, url)
        return response

    def download(
        self, path: str, dest: Path, headers: dict | None = None, sha256: str | None = None, quiet: bool = False
    ) -> Path:
        """Stream a URL to a file, through dest.part, so a half-done download never looks finished.

        A connection that drops half way, or a 5xx, is retried from the start a few times.

        Args:
            sha256: the file's expected sha256, checked before dest is written.
            quiet: no progress line on stderr.

        Raises:
            HttpError: the server answered with an error.
            ValueError: the file does not match sha256.
        """
        url = self._url(path)
        request = self._build_request("GET", url, None, headers)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        for attempt in range(len(RETRY_WAIT) + 1):
            started = time.monotonic()
            try:
                digest, size = self._stream_to_file(request, part, dest.name, quiet)
            except urllib.error.HTTPError as e:
                part.unlink(missing_ok=True)
                logger.debug("download %s: HTTP %d (%.1fs)", _no_query(url), e.code, time.monotonic() - started)
                if e.code in RETRY_STATUS and attempt < len(RETRY_WAIT):
                    self._retry(attempt, url, f"HTTP {e.code}")
                    continue
                raise HttpError(e.code, e.read().decode("utf-8", "replace"), url) from None
            except NETWORK_ERRORS as e:
                part.unlink(missing_ok=True)
                logger.debug("download %s: %s (%.1fs)", _no_query(url), e, time.monotonic() - started)
                if attempt == len(RETRY_WAIT):
                    raise
                self._retry(attempt, url, str(e))
                continue
            logger.debug("download %s: %d bytes (%.1fs)", _no_query(url), size, time.monotonic() - started)
            break
        if sha256 and digest.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            raise ValueError(f"{url}: sha256 mismatch (expected {sha256}, got {digest.hexdigest()})")
        part.replace(dest)
        return dest

    # -- one request ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else self.base + path

    def _send(self, method: str, url: str, data: bytes | Path | StreamedBody | None, headers: dict | None) -> Response:
        """One try: a status the server sent is a Response, whatever it is."""
        if isinstance(data, StreamedBody):
            size = {"Content-Length": str(data.size)}  # urllib sends an iterable body only with a known length
            return self._open(self._build_request(method, url, data, {**(headers or {}), **size}))
        if isinstance(data, Path):
            with open(data, "rb") as body:
                size = {"Content-Length": str(data.stat().st_size)}
                return self._open(self._build_request(method, url, body, {**(headers or {}), **size}))
        return self._open(self._build_request(method, url, data, headers))

    def _open(self, request: urllib.request.Request) -> Response:
        try:
            with self._opener.open(request, timeout=self.timeout) as resp:
                return Response(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, dict(e.headers), e.read())

    def _build_request(self, method: str, url: str, data, headers: dict | None) -> urllib.request.Request:
        request = urllib.request.Request(url, data=data, method=method, headers=self._headers(headers))
        if self.host_header:
            request.add_unredirected_header("Host", self.host_header)
        return request

    def _headers(self, extra: dict | None) -> dict:
        headers = {"User-Agent": "outpost"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        elif self.basic:
            token = base64.b64encode(f"{self.basic[0]}:{self.basic[1]}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        headers.update(extra or {})
        return headers

    def _stream_to_file(self, request: urllib.request.Request, part: Path, name: str, quiet: bool):
        """Write the response body to part, chunk by chunk. Returns its sha256 and size."""
        digest = hashlib.sha256()
        done = 0
        with self._opener.open(request, timeout=self.timeout) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            show_progress = bool(total) and not quiet and sys.stderr.isatty()
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if show_progress:
                    print(f"\r    {name}: {done * 100 // total}%", end="", file=sys.stderr)
            if show_progress:
                print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
        return digest, done

    # -- retries ------------------------------------------------------------------------
    def _retry(self, attempt: int, url: str, why: str) -> None:
        """Say on stderr why and when the request is tried again, then wait."""
        wait = RETRY_WAIT[attempt]
        print(
            f"    {why} from {_no_query(url)[:80]}; retrying in {wait}s ({attempt + 1}/{len(RETRY_WAIT)})",
            file=sys.stderr,
            flush=True,
        )
        self._sleep(wait)

    _sleep = staticmethod(time.sleep)  # tests replace it


def sha256_file(path: Path) -> str:
    """The sha256 of a file, read in chunks so a big file does not fill the memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _no_query(url: str) -> str:
    """The URL without its query, which may hold a token or a digest."""
    return url.split("?")[0]
