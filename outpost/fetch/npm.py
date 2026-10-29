"""npm tarballs. Lockfiles say the exact URL; a bare name@version asks the registry for it."""

import base64
import hashlib
from pathlib import Path
from urllib.parse import quote

from outpost.config import Mirrors
from outpost.http import CHUNK, Http
from outpost.needs import NpmNeed

NPMJS = "https://registry.npmjs.org"


def fetch(needs: list[NpmNeed], box: Path, mirrors: Mirrors, log=print) -> dict:
    """Download each tarball that is not in the box yet, and check it against its integrity when known.

    Returns:
        {"downloaded": n, "skipped": n, "failed": ["<name>@<version>: <why>", ...]}.
    """
    http = Http()
    done, skipped, failed = 0, 0, []
    for need in needs:
        dest = box / "npm" / tarball_name(need.name, need.version)
        if dest.exists():
            skipped += 1
            continue
        try:
            url, integrity = _tarball_url(http, need, mirrors)
            http.download(url, dest, quiet=True)
            check_integrity(dest, integrity)
            done += 1
        except Exception as e:  # keep going; the summary lists it
            dest.unlink(missing_ok=True)
            failed.append(f"{need.name}@{need.version}: {e}")
            log(f"    FAILED {need.name}@{need.version}: {e}")
    log(f"    {done} downloaded, {skipped} already in box, {len(failed)} failed")
    return {"downloaded": done, "skipped": skipped, "failed": failed}


def tarball_name(name: str, version: str) -> str:
    """The tarball's file name in box/npm: @scope/pkg 1.0.0 -> scope-pkg-1.0.0.tgz."""
    return f"{name.replace('/', '-').lstrip('@')}-{version}.tgz"


def check_integrity(path: Path, integrity: str | None) -> None:
    """Check a file against an npm integrity string (sha512-<base64>). Nothing to check when it is None.

    Raises:
        ValueError: the file does not match.
    """
    if not integrity:
        return
    algorithm, _, expected = integrity.partition("-")
    digest = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    if base64.b64encode(digest.digest()).decode() != expected:
        raise ValueError(f"integrity mismatch ({algorithm})")


def _tarball_url(http: Http, need: NpmNeed, mirrors: Mirrors) -> tuple[str, str | None]:
    """Where to download need from, and its integrity: from the lockfile when known, else from the registry."""
    if need.resolved:
        url = need.resolved
        if url.startswith(NPMJS) and mirrors.npm != NPMJS:
            url = mirrors.npm + url[len(NPMJS) :]
        return url, need.integrity
    meta = http.get(f"{mirrors.npm}/{quote(need.name, safe='@')}/{need.version}", ok=(200,)).json()
    return meta["dist"]["tarball"], meta["dist"].get("integrity")
