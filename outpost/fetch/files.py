"""Plain downloads: binaries, installers, release tarballs. Stored under files/<name>/<version>/."""

from pathlib import Path

from ..http import Http


def rewrite(url: str, mirrors) -> str:
    """Use the GitHub mirror for github.com links when one is configured."""
    if url.startswith("https://github.com/") and mirrors.github != "https://github.com":
        return mirrors.github + url[len("https://github.com") :]
    return url


def fetch(needs, box: Path, mirrors, log=print) -> dict:
    http = Http()
    done, skipped = 0, 0
    for need in needs:
        dest = box / "files" / need.name / need.version / need.filename
        if dest.exists():
            skipped += 1
            continue
        url = rewrite(need.url, mirrors)
        log(f"    {need.name} {need.version}  <- {url}")
        http.download(url, dest)
        done += 1
    log(f"    {done} downloaded, {skipped} already in box")
    return {"downloaded": done, "skipped": skipped}
