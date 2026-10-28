"""Plain downloads: binaries, installers, release tarballs. Stored under box/files/<name>/<version>/."""

from pathlib import Path

from outpost.config import Mirrors
from outpost.http import Http
from outpost.needs import FileNeed


def fetch(needs: list[FileNeed], box: Path, mirrors: Mirrors, log=print) -> dict:
    """Download each file that is not in the box yet. One file that fails does not stop the others.

    Returns:
        {"downloaded": n, "skipped": n, "failed": ["<name> <version>: <why>", ...]}.
    """
    http = Http()
    done, skipped, failed = 0, 0, []
    for need in needs:
        dest = box / "files" / need.name / need.version / need.filename
        if dest.exists():
            skipped += 1
            continue
        url = rewrite(need.url, mirrors)
        log(f"    {need.name} {need.version}  <- {url}")
        try:
            http.download(url, dest)
            done += 1
        except Exception as e:  # keep going; the summary lists it
            failed.append(f"{need.name} {need.version}: {e}")
            log(f"    FAILED {need.name} {need.version}: {e}")
    log(f"    {done} downloaded, {skipped} already in box" + (f", {len(failed)} failed" if failed else ""))
    return {"downloaded": done, "skipped": skipped, "failed": failed}


def rewrite(url: str, mirrors: Mirrors) -> str:
    """Use the GitHub mirror for github.com links when one is configured."""
    if url.startswith("https://github.com/") and mirrors.github != "https://github.com":
        return mirrors.github + url[len("https://github.com") :]
    return url
