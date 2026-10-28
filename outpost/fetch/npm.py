"""npm tarballs. Lockfiles tell us the exact URL; bare name@version asks the registry for it."""

import base64
import hashlib
from pathlib import Path
from urllib.parse import quote

from ..http import Http

NPMJS = "https://registry.npmjs.org"


def tarball_name(name: str, version: str) -> str:
    return f"{name.replace('/', '-').lstrip('@')}-{version}.tgz"


def check_integrity(data: bytes, integrity: str | None) -> None:
    if not integrity:
        return
    algo, _, expected = integrity.partition("-")
    digest = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if digest != expected:
        raise ValueError(f"integrity mismatch ({algo})")


def fetch(needs, box: Path, mirrors, log=print) -> dict:
    http = Http()
    done, skipped, failed = 0, 0, []
    for need in needs:
        dest = box / "npm" / tarball_name(need.name, need.version)
        if dest.exists():
            skipped += 1
            continue
        try:
            url = need.resolved
            if url and url.startswith(NPMJS) and mirrors.npm != NPMJS:
                url = mirrors.npm + url[len(NPMJS) :]
            if not url:
                meta = http.get(f"{mirrors.npm}/{quote(need.name, safe='@')}/{need.version}", ok=(200,)).json()
                url = meta["dist"]["tarball"]
                need = type(need)(need.name, need.version, url, meta["dist"].get("integrity"))
            http.download(url, dest, quiet=True)
            check_integrity(dest.read_bytes(), need.integrity)
            done += 1
        except Exception as e:
            dest.unlink(missing_ok=True)
            failed.append(f"{need.name}@{need.version}: {e}")
            log(f"    FAILED {need.name}@{need.version}: {e}")
    log(f"    {done} downloaded, {skipped} already in box, {len(failed)} failed")
    return {"downloaded": done, "skipped": skipped, "failed": failed}
