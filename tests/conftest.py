"""Shared test doubles: a fake SSH host and a fake Gitea / container registry speaking real HTTP."""

import json
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from outpost.ssh import CommandFailed, Result

# Gitea's image name rule below the owner, from go-gitea/gitea v28.2.0 routers/api/packages/container/container.go
GITEA_IMAGE_NAME = re.compile(r"\A[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*\Z")


class FakeHost:
    """Records every script and file instead of touching a server. Answers come from `responses`:
    a list of (regex, stdout) pairs matched against the script, first match wins."""

    def __init__(self, alias="node-1", facts=None, responses=None):
        self.alias = alias
        self._facts = facts or {"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.11", "hostname": alias}
        self.responses = list(responses or [])
        self.scripts: list[str] = []
        self.files: dict[str, str] = {}
        self.puts: list[tuple[str, str]] = []
        self.gets: list[tuple[str, str]] = []

    def run(self, script, raise_on_failure=False, timeout=0):
        self.scripts.append(script)
        for pattern, stdout in self.responses:
            if re.search(pattern, script, re.S):
                if isinstance(stdout, Result):
                    if raise_on_failure and not stdout.ok:
                        raise CommandFailed(f"[{self.alias}] command failed (exit {stdout.returncode}):\n{stdout.stderr.strip()}", stdout)
                    return stdout
                return Result(0, stdout, "")
        return Result(0, "", "")

    def write(self, path, content, mode="0644"):
        self.files[path] = content if isinstance(content, str) else content.decode()

    def put(self, local, remote, mode="0644", via="/tmp"):
        self.puts.append((str(local), remote))

    def get(self, remote, local):
        self.gets.append((remote, str(local)))
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(b"archive-bytes")
        return local

    def ip(self):
        return self._facts["ip"]

    def facts(self):
        return dict(self._facts)

    def ran(self, text: str) -> bool:
        return any(text in s for s in self.scripts)


class FakeGitea:
    """Enough of Gitea's API and OCI registry to test uploads and pulls, in memory."""

    def __init__(self, require_token=True):
        self.require_token = require_token
        self.orgs = {"pkgs"}
        self.generic: dict[str, bytes] = {}
        self.pypi: list[dict] = []
        self.npm: dict[str, dict] = {}
        self.debian: dict[str, list[str]] = {}
        self.alpine: dict[str, list[str]] = {}      # "branch/repository" -> uploaded files
        self.rpm: dict[str, list[str]] = {}         # group ("" for none) -> uploaded files
        self.alpine_key_name = "pkgs@6f2a9c1e.rsa.pub"   # Gitea names the key <owner>@<fingerprint>.rsa.pub
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.manifests: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.uploads: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.server = None
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
            index_doc = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [
                {"mediaType": entry["mediaType"], "digest": "sha256:" + "0" * 64, "size": 1, "platform": {"os": "linux", "architecture": "arm64"}},
                {"mediaType": entry["mediaType"], "digest": entry["digest"], "size": entry["size"], "platform": {"os": "linux", "architecture": "amd64"}},
            ]}
            self.manifests[(repo, tag)] = (json.dumps(index_doc).encode(), index_doc["mediaType"])
        else:
            self.manifests[(repo, tag)] = (manifest_bytes, entry["mediaType"])


class _Handler(BaseHTTPRequestHandler):
    gitea: FakeGitea = None

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
        if self.path.startswith("/v2/") and self.path not in ("/v2/token",) and not self.path.startswith("/token"):
            if self.gitea.require_token and not auth.startswith("Bearer good-token"):
                self._send(401, b'{"errors":[{"code":"UNAUTHORIZED"}]}',
                           {"WWW-Authenticate": f'Bearer realm="{self.gitea.url}/token",service="fake",scope="repository:x:pull,push"'})
                return False
        return True

    def do_GET(self):
        g = self.gitea
        g.requests.append(("GET", self.path))
        if self.path in ("/token", "/v2/token") or self.path.startswith(("/token?", "/v2/token?")):
            return self._send(200, json.dumps({"token": "good-token"}).encode())
        if self.path == "/api/v1/version":
            return self._send(200, b'{"version":"28.2.0"}')
        m = re.match(r"^/api/v1/orgs/([^/]+)$", self.path)
        if m:
            return self._send(200 if m[1] in g.orgs else 404, b"{}")
        m = re.match(r"^/api/packages/([^/]+)/debian/repository.key$", self.path)
        if m:
            return self._send(200, b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n")
        m = re.match(r"^/api/packages/([^/]+)/debian/dists/([^/]+)/Release$", self.path)
        if m:
            return self._send(200 if m[2] in g.debian else 404, b"Release")
        m = re.match(r"^/api/packages/([^/]+)/alpine/key$", self.path)
        if m:
            return self._send(200, b"-----BEGIN PUBLIC KEY-----\nfake-rsa\n-----END PUBLIC KEY-----\n",
                              {"Content-Type": "application/x-pem-file", "Content-Disposition": f'attachment; filename="{g.alpine_key_name}"'})
        m = re.match(r"^/api/packages/([^/]+)/alpine/([^/]+)/([^/]+)/([^/]+)/APKINDEX.tar.gz$", self.path)
        if m:
            return self._send(200 if f"{m[2]}/{m[3]}" in g.alpine else 404, b"APKINDEX")
        m = re.match(r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/repository.key$", self.path)
        if m:
            return self._send(200, b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake-rpm\n-----END PGP PUBLIC KEY BLOCK-----\n")
        m = re.match(r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/repodata/repomd.xml$", self.path)
        if m:
            return self._send(200 if (m[2] or "") in g.rpm else 404, b"<repomd/>")
        if not self._authed():
            return None
        m = re.match(r"^/v2/(.+)/manifests/([^/]+)$", self.path)
        if m and (m[1], m[2]) in g.manifests:
            body, mt = g.manifests[(m[1], m[2])]
            return self._send(200, body, {"Content-Type": mt, "Docker-Content-Digest": "sha256:" + __import__("hashlib").sha256(body).hexdigest()})
        m = re.match(r"^/v2/(.+)/blobs/(sha256:[0-9a-f]+)$", self.path)
        if m and (m[1], m[2]) in g.blobs:
            return self._send(200, g.blobs[(m[1], m[2])], {"Content-Type": "application/octet-stream"})
        return self._send(404, b"not found")

    def do_HEAD(self):
        g = self.gitea
        g.requests.append(("HEAD", self.path))
        if not self._authed():
            return None
        m = re.match(r"^/v2/(.+)/blobs/(sha256:[0-9a-f]+)$", self.path)
        if m:
            return self._send(200 if (m[1], m[2]) in g.blobs else 404)
        return self._send(404)

    def do_POST(self):
        g = self.gitea
        g.requests.append(("POST", self.path))
        body = self._body()
        if self.path == "/api/v1/orgs":
            g.orgs.add(json.loads(body)["username"])
            return self._send(201, b"{}")
        m = re.match(r"^/api/packages/([^/]+)/pypi$", self.path)
        if m:
            msg = BytesParser(policy=email_default).parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            fields = {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fields[name] = part.get_filename() if part.get_filename() else part.get_payload(decode=True).decode()
            if any(p["name"] == fields["name"] and p["version"] == fields["version"] and p["content"] == fields["content"] for p in g.pypi):
                return self._send(409, b"exists")
            g.pypi.append(fields)
            return self._send(201, b"")
        if not self._authed():
            return None
        m = re.match(r"^/v2/(.+)/blobs/uploads/$", self.path)
        if m:
            owner, _, image = m[1].partition("/")
            if not GITEA_IMAGE_NAME.match(image):   # what real Gitea answers for library--python
                return self._send(400, b'{"errors":[{"code":"NAME_INVALID","message":""}]}')
            uid = str(len(g.uploads) + 1)
            g.uploads[uid] = m[1]
            return self._send(202, b"", {"Location": f"/v2/{m[1]}/blobs/uploads/{uid}"})
        return self._send(404, b"")

    def do_PUT(self):
        g = self.gitea
        g.requests.append(("PUT", self.path))
        body = self._body()
        m = re.match(r"^/api/packages/([^/]+)/generic/([^/]+)/([^/]+)/([^/]+)$", self.path)
        if m:
            key = f"{m[1]}/{m[2]}/{m[3]}/{m[4]}"
            if key in g.generic:
                return self._send(409, b"exists")
            g.generic[key] = body
            return self._send(201, b"")
        m = re.match(r"^/api/packages/([^/]+)/npm/(.+)$", self.path)
        if m:
            doc = json.loads(body)
            version = next(iter(doc["versions"]))
            key = f"{doc['name']}@{version}"
            if key in g.npm:
                return self._send(409, b"exists")
            g.npm[key] = doc
            return self._send(201, b"")
        m = re.match(r"^/api/packages/([^/]+)/debian/pool/([^/]+)/([^/]+)/upload$", self.path)
        if m:
            name = str(len(body)) + ":" + body[:40].hex()
            if name in g.debian.setdefault(m[2], []):
                return self._send(409, b"exists")
            g.debian[m[2]].append(name)
            return self._send(201, b"")
        m = re.match(r"^/api/packages/([^/]+)/alpine/([^/]+)/([^/]+)$", self.path)
        if m:
            name = str(len(body)) + ":" + body[:40].hex()
            if name in g.alpine.setdefault(f"{m[2]}/{m[3]}", []):
                return self._send(409, b"exists")
            g.alpine[f"{m[2]}/{m[3]}"].append(name)
            return self._send(201, b"")
        m = re.match(r"^/api/packages/([^/]+)/rpm(?:/(.+?))?/upload$", self.path)
        if m:
            name = str(len(body)) + ":" + body[:40].hex()
            if name in g.rpm.setdefault(m[2] or "", []):
                return self._send(409, b"exists")
            g.rpm[m[2] or ""].append(name)
            return self._send(201, b"")
        if not self._authed():
            return None
        m = re.match(r"^/v2/(.+)/blobs/uploads/(\d+)\?digest=(.+)$", self.path)
        if m:
            g.blobs[(m[1], m[3].replace("%3A", ":"))] = body
            return self._send(201, b"")
        m = re.match(r"^/v2/(.+)/manifests/([^/]+)$", self.path)
        if m:
            g.manifests[(m[1], m[2])] = (body, self.headers.get("Content-Type", ""))
            return self._send(201, b"")
        return self._send(404, b"")


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
def platform_dir(tmp_path):
    """A platform folder made with `outpost init`."""
    from outpost.cli import main
    assert main(["--dir", str(tmp_path), "init"]) == 0
    return tmp_path


@pytest.fixture(scope="session")
def ca_pem(tmp_path_factory):
    """A real CA certificate (openssl is required anyway), for code that hashes or parses the CA."""
    from outpost import pki
    d = tmp_path_factory.mktemp("pki")
    return pki.ensure_ca(d / "pki", d / "secrets").read_text()
