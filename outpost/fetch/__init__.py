"""Download what the needs files ask for into the box. Runs wherever there is internet.

Each kind is one module with a fetch(...) function. They are independent, so a failure in
one kind does not stop the others; the summary at the end says what to look at.
"""

from pathlib import Path

from ..config import Mirrors
from ..needs import Needs
from . import apk, apt, files, images, npm, pypi, rpm
from .. import box as boxmod


def fetch_all(needs: Needs, box: Path, mirrors: Mirrors, only: set[str] | None = None, log=print) -> dict:
    box.mkdir(parents=True, exist_ok=True)
    plan = {
        "files": lambda: files.fetch(needs.files, box, mirrors, log=log),
        "images": lambda: images.fetch(needs.images, box, mirrors, log=log),
        "pypi": lambda: pypi.fetch(needs.pypi, box, mirrors, log=log),
        "npm": lambda: npm.fetch(needs.npm, box, mirrors, log=log),
        "apt": lambda: apt.fetch(needs.apt, box, mirrors, log=log),
        "apk": lambda: apk.fetch(needs.apk, box, mirrors, log=log),
        "rpm": lambda: rpm.fetch(needs.rpm, box, mirrors, log=log),
    }
    results: dict[str, dict] = {}
    for kind, step in plan.items():
        if only and kind not in only:
            continue
        log(f"==> {kind}")
        try:
            results[kind] = step()
        except Exception as e:  # keep going; report at the end
            results[kind] = {"error": str(e)}
            log(f"    ERROR: {e}")
    manifest = boxmod.write_manifest(box)
    results["manifest"] = {"files": manifest["files"], "bytes": manifest["bytes"]}
    return results
