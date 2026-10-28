"""One registry endpoint: pull an image into an OCI layout, push a layout, with token handling.

Pulling: GET the manifest (pick linux/<arch> from a multi-arch index), then GET every blob.
Pushing: HEAD each blob, upload the missing ones from disk, then PUT the manifest.
Auth:    on 401 the registry says where to get a token; the token is fetched and the call sent again.
"""

import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode

from outpost import versions
from outpost.http import Http, HttpError, Response
from outpost.oci.layout import blob_path, blobs_dir, digest_of, write_index
from outpost.oci.media_types import ACCEPT, INDEX_TYPES, MANIFEST_TYPES
from outpost.oci.refs import Ref


class Registry:
    """One registry endpoint with automatic token handling."""

    def __init__(self, http: Http, token_url: str | None = None):
        """
        Args:
            token_url: where to get tokens (Gitea's /v2/token), instead of the realm the registry names.
        """
        self.http = http
        self.token_url = token_url
        self._tokens: dict[str, str] = {}

    def call(
        self, method: str, path: str, repo: str, data: bytes | Path | None = None, headers: dict | None = None
    ) -> Response:
        """One request; on 401, get a token for repo and send it again. A Path body is streamed from disk."""
        resp = self.http.request(method, path, data=data, headers=headers)
        if resp.status == 401:
            challenge = next((v for k, v in resp.headers.items() if k.lower() == "www-authenticate"), "")
            if self._authorize(challenge, repo):
                resp = self.http.request(method, path, data=data, headers=headers)
        return resp

    # -- pull ---------------------------------------------------------------------
    def pull(self, ref: Ref, dest: Path, arch: str | None = None) -> Path:
        """Pull ref for linux/arch (default: the box's CPU type) into an OCI layout at dest.

        Blobs already in dest at the right size are not fetched again.

        Raises:
            HttpError: the registry refused the manifest or a blob.
            ValueError: a multi-arch image has no linux/arch image.
        """
        arch = arch or versions.ARCH
        blobs_dir(dest).mkdir(parents=True, exist_ok=True)
        manifest_bytes, media_type = self._get_manifest(ref, ref.tag)
        manifest = json.loads(manifest_bytes)
        if media_type in INDEX_TYPES or "manifests" in manifest:
            chosen = _pick_arch(ref, manifest, arch)
            manifest_bytes, media_type = self._get_manifest(ref, chosen["digest"])
            manifest = json.loads(manifest_bytes)
            media_type = manifest.get("mediaType") or chosen["mediaType"]
        blob_path(dest, digest_of(manifest_bytes)).write_bytes(manifest_bytes)
        self._fetch_blobs(ref.repo, manifest, dest)
        write_index(dest, manifest_bytes, media_type, ref.short)
        return dest

    def _get_manifest(self, ref: Ref, tag_or_digest: str) -> tuple[bytes, str]:
        """The manifest (or index) bytes, and its media type."""
        resp = self.call("GET", f"/v2/{ref.repo}/manifests/{tag_or_digest}", ref.repo, headers={"Accept": ACCEPT})
        what = f"{ref} manifest" if tag_or_digest == ref.tag else f"{ref} manifest {tag_or_digest}"
        if resp.status != 200:
            raise HttpError(resp.status, resp.text, what)
        media_type = json.loads(resp.body).get("mediaType") or resp.headers.get("Content-Type", "").split(";")[0]
        return resp.body, media_type

    def _fetch_blobs(self, repo: str, manifest: dict, dest: Path) -> None:
        """The config and every layer, streamed to disk; blobs already there at the right size are kept."""
        for blob in [manifest["config"]] + manifest.get("layers", []):
            target = blob_path(dest, blob["digest"])
            if target.exists() and target.stat().st_size == blob.get("size", target.stat().st_size):
                continue
            self._fetch_blob(repo, blob["digest"], target)

    def _fetch_blob(self, repo: str, digest: str, target: Path) -> None:
        # A HEAD through call() gets the token; download() then streams to disk with it.
        probe = self.call("HEAD", f"/v2/{repo}/blobs/{digest}", repo)
        if probe.status not in (200, 307, 302):
            raise HttpError(probe.status, probe.text, f"{repo} blob {digest}")
        self.http.download(f"/v2/{repo}/blobs/{digest}", target, sha256=digest.removeprefix("sha256:"), quiet=True)

    # -- push ---------------------------------------------------------------------
    def push(self, layout: Path, repo: str, tag: str) -> str:
        """Push an OCI layout folder as repo:tag; blobs the registry has are not sent again.

        Returns:
            The manifest's digest.

        Raises:
            HttpError: the registry refused a blob or the manifest.
        """
        entry = json.loads((layout / "index.json").read_text())["manifests"][0]
        manifest_bytes = blob_path(layout, entry["digest"]).read_bytes()
        manifest = json.loads(manifest_bytes)
        for blob in [manifest["config"]] + manifest.get("layers", []):
            self._push_blob(repo, blob["digest"], blob_path(layout, blob["digest"]))
        media_type = entry.get("mediaType") or manifest.get("mediaType") or MANIFEST_TYPES[0]
        resp = self.call(
            "PUT", f"/v2/{repo}/manifests/{tag}", repo, data=manifest_bytes, headers={"Content-Type": media_type}
        )
        if resp.status not in (200, 201):
            raise HttpError(resp.status, resp.text, f"push {repo}:{tag}")
        return entry["digest"]

    def _push_blob(self, repo: str, digest: str, path: Path) -> None:
        """Upload one blob, streamed from its file, unless the registry has it already."""
        if self.call("HEAD", f"/v2/{repo}/blobs/{digest}", repo).status == 200:
            return
        start = self.call("POST", f"/v2/{repo}/blobs/uploads/", repo, data=b"")
        if start.status != 202:
            raise HttpError(start.status, start.text, f"start upload {repo}")
        location = start.headers.get("Location")
        if not location:
            raise HttpError(start.status, "no Location header in the answer", f"start upload {repo}")
        sep = "&" if "?" in location else "?"
        resp = self.call(
            "PUT",
            f"{location}{sep}digest={quote(digest, safe=':')}",
            repo,
            data=path,
            headers={"Content-Type": "application/octet-stream"},
        )
        if resp.status not in (201, 204):
            raise HttpError(resp.status, resp.text, f"upload blob {digest}")

    # -- tokens -------------------------------------------------------------------
    def _authorize(self, challenge: str, repo: str) -> bool:
        """Answer a Bearer challenge: get (or reuse) a token for its scope. False when that is not possible."""
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
        saved_bearer, self.http.bearer = self.http.bearer, None  # the token request uses Basic or nothing
        resp = self.http.get(f"{realm}?{urlencode(query)}")
        if resp.status != 200:
            self.http.bearer = saved_bearer
            return False
        token = resp.json().get("token") or resp.json().get("access_token")
        self._tokens[scope] = token
        self.http.bearer = token
        return True


def _pick_arch(ref: Ref, index: dict, arch: str) -> dict:
    """The linux/arch entry of a multi-arch index.

    Raises:
        ValueError: there is none.
    """
    for entry in index["manifests"]:
        platform = entry.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") == arch:
            return entry
    raise ValueError(f"{ref}: no linux/{arch} image in the manifest list")
