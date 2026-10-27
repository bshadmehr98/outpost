"""Pull and push container images with nothing but HTTP. A tiny, readable skopeo.

Images are stored on disk in the standard OCI layout, which Docker and skopeo also understand:

    box/images/python_3.12-slim/
      oci-layout
      index.json                 points at the manifest, remembers the original tag
      blobs/sha256/<digest>      manifest, config and compressed layers, verbatim

Pulling: GET manifest (pick linux/amd64 from a multi-arch index), then GET every blob.
Pushing: HEAD each blob, upload the missing ones, then PUT the manifest.
Auth:    on 401 the registry says where to get a token; we fetch one and retry.
"""

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from . import versions
from .http import Http, HttpError

MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
ACCEPT = ", ".join(MANIFEST_TYPES + INDEX_TYPES)
REF_ANNOTATION = "org.opencontainers.image.ref.name"
OWNER_SEP = "--"   # joins the original owner and the image name inside Gitea: library--python, bitnami--redis


@dataclass(frozen=True)
class Ref:
    registry: str   # docker.io, ghcr.io, gitea.internal
    repo: str       # library/python, grafana/grafana
    tag: str        # 3.12-slim

    def __str__(self) -> str:
        return f"{self.registry}/{self.repo}:{self.tag}"

    @property
    def short(self) -> str:
        """How people write it: python:3.12-slim, ghcr.io/org/app:1."""
        repo = self.repo
        if self.registry == "docker.io":
            return f"{repo[8:] if repo.startswith('library/') else repo}:{self.tag}"
        return f"{self.registry}/{repo}:{self.tag}"

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def image(self) -> str:
        return self.repo.split("/", 1)[1] if "/" in self.repo else self.repo

    @property
    def safe_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.short)

    @property
    def mirror_name(self) -> str:
        """The repository name inside the Gitea organisation: `library--python`, `bitnami--redis`, `org--app`.

        Every image lives under the one organisation next to pip, npm and apt, so the original owner has
        to stay inside the name: `library/redis` (the official image) and `bitnami/redis` are different
        images and must not collide as `pkgs/redis`. A run of hyphens is valid in an OCI name component,
        and no registry allows `--` at the start or end of a component, so the split is unambiguous.
        """
        return f"{self.owner}{OWNER_SEP}{self.image}"


def parse_ref(ref: str) -> Ref:
    """python:3.12-slim -> docker.io/library/python:3.12-slim. Digest references are not supported."""
    if "@" in ref:
        raise ValueError(f"{ref}: use a tag, not a digest")
    name, tag = (ref.rsplit(":", 1) if ":" in ref.rsplit("/", 1)[-1] else (ref, "latest"))
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "docker.io", name
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return Ref(registry=registry, repo=repo, tag=tag)


def gitea_ref(gitea, ref: Ref) -> str:
    """Where `ref` lives once uploaded: python:3.12-slim -> gitea.internal/pkgs/library--python:3.12-slim.

    `gitea` is a GiteaInfo (name and org). This is the name Dockerfiles, compose, stack and k8s files
    write; nothing on a node rewrites a plain Docker Hub name, and a sealed node rejects Docker Hub."""
    return f"{gitea.name}/{gitea.org}/{ref.mirror_name}:{ref.tag}"


def original_ref(name: str) -> str | None:
    """The inverse of `Ref.mirror_name`: library--python -> python, bitnami--redis -> bitnami/redis.

    `name` is the last path component without the tag. None when it carries no owner separator."""
    if OWNER_SEP not in name:
        return None
    owner, image = name.split(OWNER_SEP, 1)
    if not owner or not image:
        return None
    return image if owner == "library" else f"{owner}/{image}"


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Registry:
    """One registry endpoint with automatic token handling."""

    def __init__(self, http: Http, token_url: str | None = None):
        self.http = http
        self.token_url = token_url  # Gitea: fetch the token directly, do not follow the realm
        self._tokens: dict[str, str] = {}

    def _authorize(self, challenge: str, repo: str) -> bool:
        if not challenge.lower().startswith("bearer"):
            return False
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        scope = params.get("scope") or f"repository:{repo}:pull,push"
        if scope in self._tokens:
            self.http.bearer = self._tokens[scope]
            return True
        realm = self.token_url or params.get("realm")
        if not realm:
            return False
        query = {"service": params.get("service", ""), "scope": scope}
        saved_bearer, self.http.bearer = self.http.bearer, None  # token request uses Basic or nothing
        resp = self.http.get(f"{realm}?{urlencode(query)}")
        if resp.status != 200:
            self.http.bearer = saved_bearer
            return False
        token = resp.json().get("token") or resp.json().get("access_token")
        self._tokens[scope] = token
        self.http.bearer = token
        return True

    def call(self, method: str, path: str, repo: str, data: bytes | None = None, headers: dict | None = None):
        resp = self.http.request(method, path, data=data, headers=headers)
        if resp.status == 401:
            challenge = next((v for k, v in resp.headers.items() if k.lower() == "www-authenticate"), "")
            if self._authorize(challenge, repo):
                resp = self.http.request(method, path, data=data, headers=headers)
        return resp

    # -- pull ---------------------------------------------------------------
    def pull(self, ref: Ref, dest: Path, arch: str | None = None) -> Path:
        arch = arch or versions.ARCH
        dest.mkdir(parents=True, exist_ok=True)
        blobs = dest / "blobs" / "sha256"
        blobs.mkdir(parents=True, exist_ok=True)

        resp = self.call("GET", f"/v2/{ref.repo}/manifests/{ref.tag}", ref.repo, headers={"Accept": ACCEPT})
        if resp.status != 200:
            raise HttpError(resp.status, resp.text, f"{ref} manifest")
        manifest_bytes = resp.body
        manifest = json.loads(manifest_bytes)
        media_type = manifest.get("mediaType") or resp.headers.get("Content-Type", "").split(";")[0]

        if media_type in INDEX_TYPES or "manifests" in manifest:
            chosen = next((m for m in manifest["manifests"]
                           if m.get("platform", {}).get("os") == "linux" and m["platform"].get("architecture") == arch), None)
            if not chosen:
                raise ValueError(f"{ref}: no linux/{arch} image in the manifest list")
            resp = self.call("GET", f"/v2/{ref.repo}/manifests/{chosen['digest']}", ref.repo, headers={"Accept": ACCEPT})
            if resp.status != 200:
                raise HttpError(resp.status, resp.text, f"{ref} manifest {chosen['digest']}")
            manifest_bytes = resp.body
            manifest = json.loads(manifest_bytes)
            media_type = manifest.get("mediaType") or chosen["mediaType"]

        manifest_digest = digest_of(manifest_bytes)
        (blobs / manifest_digest[7:]).write_bytes(manifest_bytes)

        for blob in [manifest["config"]] + manifest.get("layers", []):
            target = blobs / blob["digest"][7:]
            if target.exists() and target.stat().st_size == blob.get("size", target.stat().st_size):
                continue
            self._fetch_blob(ref.repo, blob["digest"], target)

        (dest / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}\n')
        index = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{
                "mediaType": media_type,
                "digest": manifest_digest,
                "size": len(manifest_bytes),
                "annotations": {REF_ANNOTATION: ref.short},
            }],
        }
        (dest / "index.json").write_text(json.dumps(index, indent=2) + "\n")
        return dest

    def _fetch_blob(self, repo: str, digest: str, target: Path) -> None:
        # A HEAD/GET through call() handles the token; download() streams to disk.
        probe = self.call("HEAD", f"/v2/{repo}/blobs/{digest}", repo)
        if probe.status not in (200, 307, 302):
            raise HttpError(probe.status, probe.text, f"{repo} blob {digest}")
        self.http.download(f"/v2/{repo}/blobs/{digest}", target, sha256=digest[7:], quiet=True)

    # -- push ---------------------------------------------------------------
    def push(self, layout: Path, repo: str, tag: str) -> str:
        """Push an OCI layout directory as repo:tag. Returns the manifest digest."""
        index = json.loads((layout / "index.json").read_text())
        entry = index["manifests"][0]
        blobs = layout / "blobs" / "sha256"
        manifest_bytes = (blobs / entry["digest"][7:]).read_bytes()
        manifest = json.loads(manifest_bytes)

        for blob in [manifest["config"]] + manifest.get("layers", []):
            self._push_blob(repo, blob["digest"], blobs / blob["digest"][7:])

        media_type = entry.get("mediaType") or manifest.get("mediaType") or MANIFEST_TYPES[0]
        resp = self.call("PUT", f"/v2/{repo}/manifests/{tag}", repo, data=manifest_bytes,
                         headers={"Content-Type": media_type})
        if resp.status not in (200, 201):
            raise HttpError(resp.status, resp.text, f"push {repo}:{tag}")
        return entry["digest"]

    def _push_blob(self, repo: str, digest: str, path: Path) -> None:
        if self.call("HEAD", f"/v2/{repo}/blobs/{digest}", repo).status == 200:
            return
        start = self.call("POST", f"/v2/{repo}/blobs/uploads/", repo, data=b"")
        if start.status != 202:
            raise HttpError(start.status, start.text, f"start upload {repo}")
        location = start.headers.get("Location")
        sep = "&" if "?" in location else "?"
        resp = self.call("PUT", f"{location}{sep}digest={quote(digest, safe=':')}", repo,
                         data=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})
        if resp.status not in (201, 204):
            raise HttpError(resp.status, resp.text, f"upload blob {digest}")


# -- building a small image from files, no Docker needed ----------------------

def build_layout(files: dict[str, tuple[bytes, int]], dest: Path, ref_name: str) -> Path:
    """Create a one-layer image from {path: (content, mode)}. Used for the config image."""
    layer_io = io.BytesIO()
    with tarfile.open(fileobj=layer_io, mode="w") as tar:
        for path in sorted(files):
            content, mode = files[path]
            info = tarfile.TarInfo(name=path.lstrip("/"))
            info.size = len(content)
            info.mode = mode
            info.mtime = 0
            tar.addfile(info, io.BytesIO(content))
    layer_tar = layer_io.getvalue()
    import gzip
    layer_gz = gzip.compress(layer_tar, mtime=0)

    config = {
        "architecture": versions.ARCH,
        "os": "linux",
        "config": {"Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]},
        "rootfs": {"type": "layers", "diff_ids": [digest_of(layer_tar)]},
        "history": [{"created_by": "outpost config image"}],
    }
    config_bytes = json.dumps(config, sort_keys=True).encode()
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_TYPES[0],
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest_of(config_bytes), "size": len(config_bytes)},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": digest_of(layer_gz), "size": len(layer_gz)}],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()

    blobs = dest / "blobs" / "sha256"
    blobs.mkdir(parents=True, exist_ok=True)
    for data in (layer_gz, config_bytes, manifest_bytes):
        (blobs / digest_of(data)[7:]).write_bytes(data)
    (dest / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}\n')
    (dest / "index.json").write_text(json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"mediaType": MANIFEST_TYPES[0], "digest": digest_of(manifest_bytes), "size": len(manifest_bytes),
                       "annotations": {REF_ANNOTATION: ref_name}}],
    }, indent=2) + "\n")
    return dest


def layout_ref(layout: Path) -> str:
    """The original reference an OCI layout was pulled as."""
    index = json.loads((layout / "index.json").read_text())
    return index["manifests"][0].get("annotations", {}).get(REF_ANNOTATION, layout.name)
