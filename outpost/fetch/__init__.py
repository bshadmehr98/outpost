"""Download what the needs files ask for into the box. Runs wherever there is internet.

Each kind is one module with a fetch(...) function. They are independent, so a failure in
one kind does not stop the others; the summary at the end says what to look at.

    results = fetch_into_box(system, all_needs(system), only=None, log=print)
    for problem in failures(results): ...
"""

from pathlib import Path

from .. import box as boxmod
from ..config import Mirrors
from ..needs import Needs
from ..system import AirGappedSystem
from ..workspace import STATE_FETCH
from . import apk, apt, files, images, npm, pypi, rpm


def fetch_into_box(system: AirGappedSystem, needs: Needs, only: set[str] | None = None, log=print) -> dict:
    """Fetch the needs into the workspace's box and record the results in state/fetch.json.

    Args:
        only: fetch only these kinds (None: all of them).

    Returns:
        The results per kind, and the manifest's size under "manifest".
    """
    results = fetch_all(needs, system.workspace.box_dir, system.mirrors, only=only, log=log)
    system.write_state(STATE_FETCH, results)
    return results


def failures(results: dict) -> list[str]:
    """What could not be fetched, one line each: a kind that failed as a whole, then single items."""
    per_kind = [f"{kind}: {result['error']}" for kind, result in results.items() if _has(result, "error")]
    per_item = [item for result in results.values() if isinstance(result, dict) for item in result.get("failed", [])]
    return per_kind + per_item


def fetch_all(needs: Needs, box: Path, mirrors: Mirrors, only: set[str] | None = None, log=print) -> dict:
    """Fetch every kind of the needs into box, then write the box's manifest. Returns the results per kind."""
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


def _has(result, key: str) -> bool:
    return isinstance(result, dict) and bool(result.get(key))
