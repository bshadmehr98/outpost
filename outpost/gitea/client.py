"""Gitea's REST API and package registries, through plain HTTP.

Package registries we fill:                 Clients read them at:
  generic  files (binaries, tarballs)       https://gitea/api/packages/ORG/generic/NAME/VERSION/FILE
  pypi     wheels and sdists                https://gitea/api/packages/ORG/pypi/simple
  npm      tarballs                         https://gitea/api/packages/ORG/npm/
  debian   .deb files                       deb https://gitea/api/packages/ORG/debian DISTRO main
  container images                          docker pull gitea/OWNER/IMAGE:TAG
"""

import base64
import hashlib
import json
import tarfile
import uuid
from pathlib import Path
from urllib.parse import quote

from ..http import Http, HttpError
from .. import oci


class GiteaClient:
    def __init__(self, http: Http, org: str, public_url: str | None = None):
        self.http = http
        self.org = org
        self.public_url = (public_url or http.base).rstrip("/")

    # -- basics -------------------------------------------------------------
    def version(self) -> str:
        return self.http.get("/api/v1/version", ok=(200,)).json()["version"]

    def ensure_org(self, name: str) -> bool:
        """Create a public organisation if it does not exist. Returns True when created."""
        if self.http.get(f"/api/v1/orgs/{quote(name)}").status == 200:
            return False
        body = json.dumps({"username": name, "visibility": "public", "repo_admin_change_team_access": True}).encode()
        self.http.request("POST", "/api/v1/orgs", data=body, headers={"Content-Type": "application/json"}, ok=(201,))
        return True

    def _json(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201, 204)):
        data = json.dumps(body).encode() if body is not None else None
        return self.http.request(method, path, data=data, headers={"Content-Type": "application/json"}, ok=ok)

    # -- repositories and Actions (used by the demo) ------------------------
    def ensure_repo(self, owner: str, name: str, description: str = "") -> bool:
        """Create a public repository under an organisation, with Actions on. True when created."""
        if self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(name)}").status == 200:
            return False
        self._json("POST", f"/api/v1/orgs/{quote(owner)}/repos", {"name": name, "description": description, "auto_init": False,
                                                                 "default_branch": "main", "private": False}, ok=(201,))
        self._json("PATCH", f"/api/v1/repos/{quote(owner)}/{quote(name)}", {"has_actions": True, "has_packages": True})
        return True

    def repo_files(self, owner: str, repo: str) -> dict[str, str]:
        """path -> blob sha of every file on the default branch (empty for an empty repository)."""
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/git/trees/HEAD?recursive=true&per_page=1000")
        if resp.status != 200:
            return {}
        return {e["path"]: e["sha"] for e in resp.json().get("tree") or [] if e.get("type") == "blob"}

    def commit_files(self, owner: str, repo: str, files: dict[str, bytes], message: str, existing: dict[str, str]) -> dict:
        """One commit on main that creates or updates `files` (path -> content). Returns Gitea's answer."""
        ops = [{"operation": "update" if path in existing else "create", "path": path,
                "content": base64.b64encode(content).decode(), **({"sha": existing[path]} if path in existing else {})}
               for path, content in sorted(files.items())]
        resp = self._json("POST", f"/api/v1/repos/{quote(owner)}/{quote(repo)}/contents",
                          {"branch": "main", "message": message, "files": ops}, ok=(201,))
        return resp.json()

    def create_token(self, user: str, password: str, name: str, scopes: list[str]) -> str:
        """A new API token with just these scopes. Gitea only issues tokens against a password, so this
        is the one call that uses the admin password instead of the admin token."""
        saved = self.http.basic
        self.http.basic = (user, password)
        try:
            resp = self._json("POST", f"/api/v1/users/{quote(user)}/tokens", {"name": name, "scopes": scopes}, ok=(201,))
        finally:
            self.http.basic = saved
        return resp.json()["sha1"]

    def tokens(self, user: str, password: str) -> list[dict]:
        """The user's API tokens (name and last eight characters), listed with the password like create_token."""
        saved = self.http.basic
        self.http.basic = (user, password)
        try:
            return self._json("GET", f"/api/v1/users/{quote(user)}/tokens").json()
        finally:
            self.http.basic = saved

    def set_secret(self, owner: str, repo: str, name: str, value: str) -> None:
        """An Actions secret on a repository, available to its workflows as ${{ secrets.NAME }}."""
        self._json("PUT", f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/secrets/{quote(name)}", {"data": value}, ok=(201, 204))

    def runner_registration_token(self, org: str | None = None) -> str:
        """A token that registers a runner for the organisation, so every repository in it can use the
        nodes. Organisation level needs only write:organization, where the instance level needs admin."""
        resp = self._json("POST", f"/api/v1/orgs/{quote(org or self.org)}/actions/runners/registration-token", ok=(200, 201))
        return (resp.json() or {}).get("token") or resp.headers.get("token") or resp.headers.get("Token")

    def runners(self, org: str | None = None) -> list[dict]:
        resp = self.http.get(f"/api/v1/orgs/{quote(org or self.org)}/actions/runners")
        return (resp.json() or {}).get("runners", []) if resp.status == 200 else []

    def workflow_runs(self, owner: str, repo: str, limit: int = 5) -> list[dict]:
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/runs?limit={limit}")
        return (resp.json() or {}).get("workflow_runs", []) if resp.status == 200 else []

    def workflow_jobs(self, owner: str, repo: str, run_id: int) -> list[dict]:
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/runs/{run_id}/jobs")
        return (resp.json() or {}).get("jobs", []) if resp.status == 200 else []

    # -- package registries -------------------------------------------------
    def upload_generic(self, name: str, version: str, path: Path, owner: str | None = None) -> str:
        url = f"/api/packages/{owner or self.org}/generic/{quote(name)}/{quote(version)}/{quote(path.name)}"
        resp = self.http.request("PUT", url, data=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})
        return _outcome(resp, "generic", f"{name} {version} {path.name}")

    def upload_pypi(self, path: Path, owner: str | None = None) -> str:
        name, version = pypi_name_version(path.name)
        data = path.read_bytes()
        fields = {
            ":action": "file_upload",
            "protocol_version": "1",
            "name": name,
            "version": version,
            "sha256_digest": hashlib.sha256(data).hexdigest(),
            "filetype": "bdist_wheel" if path.suffix == ".whl" else "sdist",
        }
        body, content_type = multipart(fields, "content", path.name, data)
        resp = self.http.request("POST", f"/api/packages/{owner or self.org}/pypi", data=body, headers={"Content-Type": content_type})
        return _outcome(resp, "pypi", path.name)

    def upload_npm(self, path: Path, owner: str | None = None) -> str:
        data = path.read_bytes()
        pkg = npm_package_json(path)
        name, version = pkg["name"], pkg["version"]
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        version_meta = dict(pkg)
        version_meta["dist"] = {
            "integrity": integrity,
            "shasum": hashlib.sha1(data).hexdigest(),
            "tarball": f"{self.public_url}/api/packages/{owner or self.org}/npm/{quote(name, safe='@')}/-/{version}/{path.name}",
        }
        upload = {
            "_id": name,
            "name": name,
            "description": pkg.get("description", ""),
            "dist-tags": {"latest": version},
            "versions": {version: version_meta},
            "_attachments": {path.name: {"content_type": "application/octet-stream", "data": base64.b64encode(data).decode(), "length": len(data)}},
        }
        url = f"/api/packages/{owner or self.org}/npm/{quote(name, safe='@')}"
        resp = self.http.request("PUT", url, data=json.dumps(upload).encode(), headers={"Content-Type": "application/json"})
        return _outcome(resp, "npm", f"{name}@{version}")

    def upload_debian(self, path: Path, distro: str, component: str = "main", owner: str | None = None) -> str:
        url = f"/api/packages/{owner or self.org}/debian/pool/{quote(distro)}/{quote(component)}/upload"
        resp = self.http.request("PUT", url, data=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})
        return _outcome(resp, "debian", path.name)

    def debian_key(self, owner: str | None = None) -> str:
        return self.http.get(f"/api/packages/{owner or self.org}/debian/repository.key", ok=(200,)).text

    def debian_has_distro(self, distro: str, owner: str | None = None) -> bool:
        return self.http.get(f"/api/packages/{owner or self.org}/debian/dists/{quote(distro)}/Release").status == 200

    def registry(self) -> oci.Registry:
        """A container-registry view of the same Gitea. Tokens come from /v2/token with our Basic auth."""
        return oci.Registry(self.http, token_url=f"{self.http.base}/v2/token")

    def push_image(self, layout: Path, owner: str, image: str, tag: str) -> str:
        return self.registry().push(layout, f"{owner}/{image}", tag)


def _outcome(resp, kind: str, what: str) -> str:
    """Uploads return 'uploaded' or 'exists'; anything else is an error with the server's words."""
    if resp.status in (200, 201):
        return "uploaded"
    if resp.status == 409 or (resp.status == 400 and "exist" in resp.text.lower()):
        return "exists"
    raise HttpError(resp.status, resp.text, f"{kind} upload of {what}")


# -- helpers ------------------------------------------------------------------

def multipart(fields: dict[str, str], file_field: str, filename: str, file_data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
    out += file_data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def pypi_name_version(filename: str) -> tuple[str, str]:
    """requests-2.31.0-py3-none-any.whl -> (requests, 2.31.0);  Flask-3.0.0.tar.gz -> (Flask, 3.0.0)."""
    if filename.endswith(".whl"):
        name, version = filename[:-4].split("-")[:2]
        return name, version
    for ext in (".tar.gz", ".zip", ".tar.bz2"):
        if filename.endswith(ext):
            stem = filename[: -len(ext)]
            name, _, version = stem.rpartition("-")
            return name, version
    raise ValueError(f"{filename}: not a wheel or sdist")


def npm_package_json(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/package.json") and member.name.count("/") == 1:
                return json.loads(tar.extractfile(member).read())
    raise ValueError(f"{path.name}: no package/package.json inside")
