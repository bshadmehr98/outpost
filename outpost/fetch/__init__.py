"""Download what the needs ask for into the box. The only step that needs the internet.

    results = fetch_into_box(system, all_needs(system), only=None, log=print)   # writes state/fetch.json
    for problem in failures(results): ...

Each kind is one module with a fetch(needs, box, mirrors, ...) function; the kinds whose downloads
depend on the CPU type also take the arch. The kinds are independent,
so a failure in one does not stop the others, and within a kind one package that fails does not
stop the rest; the summary at the end says what to look at.

    images.py         container images, over plain HTTP (outpost.oci)
    pypi.py           wheels and sdists, with pip download
    npm.py            npm tarballs
    apt.py apk.py rpm.py   OS packages, in a container of the distro
    in_container.py   fetch_os_packages(), run_in_container(): the docker run they share
    files.py          plain downloads
"""

from pathlib import Path

from outpost import box as boxmod
from outpost.config import Mirrors
from outpost.fetch import apk, apt, files, images, npm, pypi, rpm
from outpost.needs import Needs
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_FETCH


def fetch_into_box(system: AirGappedSystem, needs: Needs, only: set[str] | None = None, log=print) -> dict:
    """Fetch the needs into the workspace's box and record the results in state/fetch.json.

    Args:
        only: fetch only these kinds (None: all of them).

    Returns:
        The results per kind, and the manifest's size under "manifest".
    """
    results = fetch_all(needs, system.workspace.box_dir, system.mirrors, system.arch, only=only, log=log)
    system.write_state(STATE_FETCH, results)
    return results


def failures(results: dict) -> list[str]:
    """What could not be fetched, one line each: a kind that failed as a whole, then single items."""
    per_kind = [f"{kind}: {result['error']}" for kind, result in results.items() if _has(result, "error")]
    per_item = [item for result in results.values() if isinstance(result, dict) for item in result.get("failed", [])]
    return per_kind + per_item


def fetch_all(needs: Needs, box: Path, mirrors: Mirrors, arch: str, only: set[str] | None = None, log=print) -> dict:
    """Fetch every kind of the needs into box, for the CPU type arch, then write the box's manifest.
    Returns the results per kind."""
    box.mkdir(parents=True, exist_ok=True)
    plan = {
        "files": lambda: files.fetch(needs.files, box, mirrors, log=log),
        "images": lambda: images.fetch(needs.images, box, mirrors, arch, log=log),
        "pypi": lambda: pypi.fetch(needs.pypi, box, mirrors, arch, log=log),
        "npm": lambda: npm.fetch(needs.npm, box, mirrors, log=log),
        "apt": lambda: apt.fetch(needs.apt, box, mirrors, arch, log=log),
        "apk": lambda: apk.fetch(needs.apk, box, mirrors, arch, log=log),
        "rpm": lambda: rpm.fetch(needs.rpm, box, mirrors, arch, log=log),
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
