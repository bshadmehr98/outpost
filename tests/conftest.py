"""Shared test doubles: a fake SSH host and a fake Gitea / container registry speaking real HTTP."""

import hashlib
import json
import re
import threading
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from outpost.ssh import CommandFailed, Host, Result

# Gitea's image name rule below the owner, from go-gitea/gitea v28.2.0 routers/api/packages/container/container.go
GITEA_IMAGE_NAME = re.compile(r"\A[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*\Z")


class FakeHost(Host):
    """Records every script and file instead of touching a server. Answers come from `responses`:
    a list of (regex, stdout) pairs matched against the script, first match wins.

    It is a Host, so it goes wherever a Host does; every method that would start ssh or scp is replaced."""

    def __init__(self, alias: str = "node-1", facts: dict | None = None, responses: list | None = None):
        super().__init__(alias)
        self._facts = facts or {
            "id": "ubuntu",
            "codename": "noble",
            "arch": "x86_64",
            "ip": "10.0.0.11",
            "hostname": alias,
        }
        self.responses = list(responses or [])
        self.scripts: list[str] = []
        self.files: dict[str, str] = {}
        self.puts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []

    def run(self, script: str, raise_on_failure: bool = False, timeout: int = 0) -> Result:
        self.scripts.append(script)
        for pattern, stdout in self.responses:
            if re.search(pattern, script, re.S):
                if isinstance(stdout, Result):
                    if raise_on_failure and not stdout.ok:
                        raise CommandFailed(
                            f"[{self.alias}] command failed (exit {stdout.returncode}):\n{stdout.stderr.strip()}",
                            stdout,
                        )
                    return stdout
                return Result(0, stdout, "")
        return Result(0, "", "")

    def write(self, path: str, content: str | bytes, mode: str = "0644") -> None:
        self.files[path] = content if isinstance(content, str) else content.decode()

    def put(self, local: Path, remote: str, mode: str = "0644", via: str = "/tmp") -> None:
        self.puts.append((str(local), remote))

    def get(self, remote: str, local: Path) -> Path:
        self.gets.append((remote, str(local)))
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(b"archive-bytes")
        return local

    def ip(self) -> str:
        return self._facts["ip"]

    def facts(self) -> dict:
        return dict(self._facts)

    def ran(self, text: str) -> bool:
        return any(text in s for s in self.scripts)


def explained(log: str) -> dict:
    """ui.explain.explain(log), failing the test when it knows no failure in the log."""
    from outpost.ui.explain import explain

    found = explain(log)
    assert found is not None, f"no known failure in: {log!r}"
    return found


class FakeGitea:
    """Enough of Gitea's API and OCI registry to test uploads and pulls, in memory."""

    def __init__(self, require_token=True):
        self.require_token = require_token
        self.orgs = {"pkgs"}
        self.generic: dict[str, bytes] = {}
        self.pypi: list[dict] = []
        self.npm: dict[str, dict] = {}
        self.debian: dict[str, list[str]] = {}
        self.alpine: dict[str, list[str]] = {}  # "branch/repository" -> uploaded files
        self.rpm: dict[str, list[str]] = {}  # group ("" for none) -> uploaded files
        self.alpine_key_name = "pkgs@6f2a9c1e.rsa.pub"  # Gitea names the key <owner>@<fingerprint>.rsa.pub
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.manifests: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.uploads: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.server: ThreadingHTTPServer | None = None
        self.url = ""

    # registry helpers used by tests to pre-load images for pulling
    def add_layout(self, layout: Path, repo: str, tag: str, add_index: bool = False):
        index = json.loads((layout / "index.json").read_text())
        entry = index["manifests"][0]
        blobs = layout / "blobs" / "sha256"
        manifest_bytes = (blobs / entry["digest"][7:]).read_bytes()
        manifest = json.loads(manifest_bytes)
        for blob in [manifest["config"]] + manifest["layers"]:
            self.blobs[(repo, blob["digest"])] = (blobs / blob["digest"][7:]).read_bytes()
        self.manifests[(repo, entry["digest"])] = (manifest_bytes, entry["mediaType"])
        if add_index:
            index_doc = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {
                        "mediaType": entry["mediaType"],
                        "digest": "sha256:" + "0" * 64,
                        "size": 1,
                        "platform": {"os": "linux", "architecture": "arm64"},
                    },
                    {
                        "mediaType": entry["mediaType"],
                        "digest": entry["digest"],
                        "size": entry["size"],
                        "platform": {"os": "linux", "architecture": "amd64"},
                    },
                ],
            }
            self.manifests[(repo, tag)] = (json.dumps(index_doc).encode(), index_doc["mediaType"])
        else:
            self.manifests[(repo, tag)] = (manifest_bytes, entry["mediaType"])


class _Handler(BaseHTTPRequestHandler):
    gitea: FakeGitea  # set on the subclass the fake_gitea fixture makes

    def log_message(self, *_):
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _authed(self):
        auth = self.headers.get("Authorization", "")
        if (
            self.path.startswith("/v2/")
            and self.path not in ("/v2/token",)
            and not self.path.startswith("/token")
            and self.gitea.require_token
            and not auth.startswith("Bearer good-token")
        ):
            self._send(
                401,
                b'{"errors":[{"code":"UNAUTHORIZED"}]}',
                {
                    "WWW-Authenticate": f'Bearer realm="{self.gitea.url}/token",service="fake",scope="repository:x:pull,push"'
                },
            )
            return False
        return True

    def do_GET(self):
        self._route("GET", GET_OPEN, GET_WITH_TOKEN, not_found=b"not found")

    def do_HEAD(self):
        self._route("HEAD", {}, HEAD_WITH_TOKEN, not_found=b"")

    def do_POST(self):
        self._route("POST", POST_OPEN, POST_WITH_TOKEN, not_found=b"")

    def do_PUT(self):
        self._route("PUT", PUT_OPEN, PUT_WITH_TOKEN, not_found=b"")

    def _route(self, method, open_routes, token_routes, not_found):
        """The first route whose pattern matches the path answers. Routes in token_routes need the
        registry token, as Gitea's /v2/ API does; the rest are answered without it."""
        self.gitea.requests.append((method, self.path))
        body = self._body() if method in ("POST", "PUT") else b""
        for routes in (open_routes, token_routes):
            if routes is token_routes and not self._authed():
                return None
            for pattern, answer in routes.items():
                m = re.match(pattern, self.path)
                if m:
                    return answer(self, self.gitea, m, body)
        return self._send(404, not_found)

    # -- answers used by several routes --------------------------------------------------------
    def _store_once(self, files: list, body: bytes):
        """Keep an uploaded package; 409 when the same bytes were uploaded before, as Gitea answers."""
        name = str(len(body)) + ":" + body[:40].hex()
        if name in files:
            return self._send(409, b"exists")
        files.append(name)
        return self._send(201, b"")

    def _pypi_upload(self, g, body):
        msg = BytesParser(EmailMessage, policy=email_default).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
        )
        fields = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fields[name] = part.get_filename() or part.get_content()
        if any(
            p["name"] == fields["name"] and p["version"] == fields["version"] and p["content"] == fields["content"]
            for p in g.pypi
        ):
            return self._send(409, b"exists")
        g.pypi.append(fields)
        return self._send(201, b"")

    def _start_blob_upload(self, g, repo):
        owner, _, image = repo.partition("/")
        if not GITEA_IMAGE_NAME.match(image):  # what real Gitea answers for library--python
            return self._send(400, b'{"errors":[{"code":"NAME_INVALID","message":""}]}')
        uid = str(len(g.uploads) + 1)
        g.uploads[uid] = repo
        return self._send(202, b"", {"Location": f"/v2/{repo}/blobs/uploads/{uid}"})

    def _manifest(self, g, key):
        if key not in g.manifests:
            return self._send(404, b"not found")
        body, media_type = g.manifests[key]
        return self._send(200, body, {"Content-Type": media_type, "Docker-Content-Digest": _digest(body)})

    def _create_org(self, g, name: str):
        g.orgs.add(name)
        return self._send(201, b"{}")

    def _keep(self, store: dict, key, value):
        """Store a blob or a manifest; pushing the same one again replaces it."""
        store[key] = value
        return self._send(201, b"")

    def _store_once_by_key(self, store: dict, key: str, value):
        if key in store:
            return self._send(409, b"exists")
        store[key] = value
        return self._send(201, b"")


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


PGP_KEY = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n"
RPM_KEY = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake-rpm\n-----END PGP PUBLIC KEY BLOCK-----\n"
RSA_KEY = b"-----BEGIN PUBLIC KEY-----\nfake-rsa\n-----END PUBLIC KEY-----\n"

# pattern -> answer(handler, gitea, match, body); first match wins
GET_OPEN = {
    r"^/(v2/)?token(\?|$)": lambda h, g, m, body: h._send(200, json.dumps({"token": "good-token"}).encode()),
    r"^/api/v1/version$": lambda h, g, m, body: h._send(200, b'{"version":"28.2.0"}'),
    r"^/api/v1/orgs/([^/]+)$": lambda h, g, m, body: h._send(200 if m[1] in g.orgs else 404, b"{}"),
    r"^/api/packages/([^/]+)/debian/repository.key$": lambda h, g, m, body: h._send(200, PGP_KEY),
    r"^/api/packages/([^/]+)/debian/dists/([^/]+)/Release$": lambda h, g, m, body: h._send(
        200 if m[2] in g.debian else 404, b"Release"
    ),
    r"^/api/packages/([^/]+)/alpine/key$": lambda h, g, m, body: h._send(
        200,
        RSA_KEY,
        {
            "Content-Type": "application/x-pem-file",
            "Content-Disposition": f'attachment; filename="{g.alpine_key_name}"',
        },
    ),
    r"^/api/packages/([^/]+)/alpine/([^/]+)/([^/]+)/([^/]+)/APKINDEX.tar.gz$": lambda h, g, m, body: h._send(
        200 if f"{m[2]}/{m[3]}" in g.alpine else 404, b"APKINDEX"
    ),
    r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/repository.key$": lambda h, g, m, body: h._send(200, RPM_KEY),
    r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/repodata/repomd.xml$": lambda h, g, m, body: h._send(
        200 if (m[2] or "") in g.rpm else 404, b"<repomd/>"
    ),
}
GET_WITH_TOKEN = {
    r"^/v2/(.+)/manifests/([^/]+)$": lambda h, g, m, body: h._manifest(g, (m[1], m[2])),
    r"^/v2/(.+)/blobs/(sha256:[0-9a-f]+)$": lambda h, g, m, body: (
        h._send(200, g.blobs[(m[1], m[2])], {"Content-Type": "application/octet-stream"})
        if (m[1], m[2]) in g.blobs
        else h._send(404, b"not found")
    ),
}
HEAD_WITH_TOKEN = {
    r"^/v2/(.+)/blobs/(sha256:[0-9a-f]+)$": lambda h, g, m, body: h._send(200 if (m[1], m[2]) in g.blobs else 404),
    r"^/v2/(.+)/manifests/([^/]+)$": lambda h, g, m, body: (
        h._send(200, headers={"Docker-Content-Digest": _digest(g.manifests[(m[1], m[2])][0])})
        if (m[1], m[2]) in g.manifests
        else h._send(404)
    ),
}
POST_OPEN = {
    r"^/api/v1/orgs$": lambda h, g, m, body: h._create_org(g, json.loads(body)["username"]),
    r"^/api/packages/([^/]+)/pypi$": lambda h, g, m, body: h._pypi_upload(g, body),
}
POST_WITH_TOKEN = {
    r"^/v2/(.+)/blobs/uploads/$": lambda h, g, m, body: h._start_blob_upload(g, m[1]),
}
PUT_OPEN = {
    r"^/api/packages/([^/]+)/generic/([^/]+)/([^/]+)/([^/]+)$": lambda h, g, m, body: h._store_once_by_key(
        g.generic, f"{m[1]}/{m[2]}/{m[3]}/{m[4]}", body
    ),
    r"^/api/packages/([^/]+)/npm/(.+)$": lambda h, g, m, body: h._store_once_by_key(
        g.npm, _npm_key(json.loads(body)), json.loads(body)
    ),
    r"^/api/packages/([^/]+)/debian/pool/([^/]+)/([^/]+)/upload$": lambda h, g, m, body: h._store_once(
        g.debian.setdefault(m[2], []), body
    ),
    r"^/api/packages/([^/]+)/alpine/([^/]+)/([^/]+)$": lambda h, g, m, body: h._store_once(
        g.alpine.setdefault(f"{m[2]}/{m[3]}", []), body
    ),
    r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/upload$": lambda h, g, m, body: h._store_once(
        g.rpm.setdefault(m[2] or "", []), body
    ),
}
PUT_WITH_TOKEN = {
    r"^/v2/(.+)/blobs/uploads/(\d+)\?digest=(.+)$": lambda h, g, m, body: h._keep(
        g.blobs, (m[1], m[3].replace("%3A", ":")), body
    ),
    r"^/v2/(.+)/manifests/([^/]+)$": lambda h, g, m, body: h._keep(
        g.manifests, (m[1], m[2]), (body, h.headers.get("Content-Type", ""))
    ),
}


def _npm_key(doc: dict) -> str:
    return f"{doc['name']}@{next(iter(doc['versions']))}"


@pytest.fixture
def fake_gitea():
    gitea = FakeGitea()
    handler = type("Handler", (_Handler,), {"gitea": gitea})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    gitea.server = server
    gitea.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield gitea
    server.shutdown()
    server.server_close()


@pytest.fixture
def workspace_dir(tmp_path):
    """A workspace made with `outpost init`."""
    from outpost.cli import main

    assert main(["--dir", str(tmp_path), "init"]) == 0
    return tmp_path


@pytest.fixture(scope="session")
def ca_pem(tmp_path_factory):
    """A real CA certificate (openssl is required anyway), for code that hashes or parses the CA."""
    from outpost import pki
    from outpost.workspace import Workspace

    d = tmp_path_factory.mktemp("pki")
    return pki.ensure_ca(Workspace(d)).read_text()
