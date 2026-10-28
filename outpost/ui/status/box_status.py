"""What the box holds, against what the needs ask for: what is missing, new, or no longer needed."""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from outpost import oci
from outpost.box import KINDS
from outpost.fetch.npm import tarball_name
from outpost.needs import Needs
from outpost.workspace import STATE_UPLOAD, Workspace

UNNEEDED_SHOWN = 50  # box_changes lists at most this many unneeded things (and counts them all)


def box_status(workspace: Workspace, needs: Needs) -> dict:
    """Per kind: how many files and bytes the box has, and which needs are not in it yet.

    Returns:
        {"kinds": {kind: {"files", "bytes"}}, "missing": {kind: [...]}, "missing_total", "bytes", "files"}
    """
    box = workspace.box_dir
    kinds = {kind: {"files": 0, "bytes": 0} for kind in KINDS}
    for kind, path in _box_files(box):
        kinds[kind]["files"] += 1
        kinds[kind]["bytes"] += path.stat().st_size
    missing = {kind: MISSING_IN_BOX[kind](box, needs) for kind in KINDS}
    return {
        "kinds": kinds,
        "missing": missing,
        "missing_total": sum(len(found) for found in missing.values()),
        "bytes": sum(k["bytes"] for k in kinds.values()),
        "files": sum(k["files"] for k in kinds.values()),
    }


def box_changes(workspace: Workspace, needs: Needs) -> dict:
    """How many files are new in the box since the last upload (by mtime), and what the box holds that
    no needs file asks for any more, so a stale box is visible before it is uploaded."""
    box = workspace.box_dir
    uploaded_at = workspace.state_mtime(STATE_UPLOAD)
    new = [path for _, path in _box_files(box) if not uploaded_at or path.stat().st_mtime > uploaded_at + 1]
    unneeded = _unneeded_images(box, needs) + _unneeded_npm(box, needs) + _unneeded_files(box, needs)
    return {
        "new_files": len(new),
        "new_bytes": sum(path.stat().st_size for path in new),
        "unneeded": unneeded[:UNNEEDED_SHOWN],
        "unneeded_total": len(unneeded),
        "uploaded_at": datetime.fromtimestamp(uploaded_at, UTC).isoformat(timespec="seconds") if uploaded_at else None,
    }


def spec_parts(spec: str) -> tuple[str, str | None]:
    """A pip spec's normalized name and exact version: 'Flask==3.1.0' -> ('flask', '3.1.0'); 'requests>=2' -> ('requests', None)."""
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:==\s*([^\s;,]+))?", spec)
    return (_normalize(m.group(1)), m.group(2)) if m else (_normalize(spec), None)


def _box_files(box: Path) -> Iterator[tuple[str, Path]]:
    """(kind, path) for every file under box/<kind>/."""
    for kind in KINDS:
        if (box / kind).exists():
            for path in (box / kind).rglob("*"):
                if path.is_file():
                    yield kind, path


def _normalize(name: str) -> str:
    """A Python package name the way pip compares them: Flask_SQL.Alchemy -> flask-sql-alchemy."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _names_in(folder: Path, pattern: str = "*") -> list[str]:
    return [path.name for path in folder.glob(pattern)] if folder.exists() else []


# -- missing from the box: one function per kind ---------------------------------------------


def _missing_images(box: Path, needs: Needs) -> list[str]:
    """Images without an OCI layout in box/images/; a reference that does not parse is missing too."""
    missing = []
    for ref in needs.images:
        try:
            if not (box / "images" / oci.parse_ref(ref).safe_name / "index.json").exists():
                missing.append(ref)
        except ValueError:
            missing.append(ref)
    return missing


def _missing_pypi(box: Path, needs: Needs) -> list[str]:
    """Specs with no wheel or sdist whose name (and exact version, if pinned) matches."""
    files = [_normalize(name.lower()) for name in _names_in(box / "pypi")]
    missing = []
    for specs in needs.pypi.values():
        for spec in specs:
            name, version = spec_parts(spec)
            prefix = _normalize(name + "-" + (version.lower() if version else ""))
            if not any(file.startswith(prefix) for file in files):
                missing.append(spec)
    return missing


def _missing_npm(box: Path, needs: Needs) -> list[str]:
    return [f"{n.name}@{n.version}" for n in needs.npm if not (box / "npm" / tarball_name(n.name, n.version)).exists()]


def _missing_apt(box: Path, needs: Needs) -> list[str]:
    """Packages with no `<name>_<version>_<arch>.deb` in box/apt/<distro>/."""
    missing = []
    for distro, packages in needs.apt.items():
        debs = _names_in(box / "apt" / distro, "*.deb")
        missing += [f"{pkg} ({distro})" for pkg in packages if not any(d.startswith(pkg + "_") for d in debs)]
    return missing


def _missing_name_version_files(kind: str, extension: str) -> Callable[[Path, Needs], list[str]]:
    """For apk and rpm, whose files are named `<name>-<version>...`."""

    def missing_of_kind(box: Path, needs: Needs) -> list[str]:
        missing = []
        for distro, packages in getattr(needs, kind).items():
            have = _names_in(box / kind / distro, f"*{extension}")
            missing += [f"{pkg} ({distro})" for pkg in packages if not any(f.startswith(pkg + "-") for f in have)]
        return missing

    return missing_of_kind


def _missing_files(box: Path, needs: Needs) -> list[str]:
    return [
        f"{f.name} {f.version}" for f in needs.files if not (box / "files" / f.name / f.version / f.filename).exists()
    ]


MISSING_IN_BOX: dict[str, Callable[[Path, Needs], list[str]]] = {
    "images": _missing_images,
    "pypi": _missing_pypi,
    "npm": _missing_npm,
    "apt": _missing_apt,
    "apk": _missing_name_version_files("apk", ".apk"),
    "rpm": _missing_name_version_files("rpm", ".rpm"),
    "files": _missing_files,
}


# -- in the box, but no longer needed ---------------------------------------------------------
# pypi is left out on purpose: fetch pulls each package's dependencies along, and no needs file
# names those, so every one of them would look unneeded. apt, apk and rpm likewise.


def _unneeded_images(box: Path, needs: Needs) -> list[str]:
    """Image layouts no need names. Each reference is parsed on its own, so one bad one does not
    make every image look unneeded. `config` is the config image that upload builds."""
    wanted = {"config"}
    for ref in needs.images:
        try:
            wanted.add(oci.parse_ref(ref).safe_name)
        except ValueError:
            continue
    folder = box / "images"
    if not folder.exists():
        return []
    return [f"image {path.name}" for path in folder.iterdir() if path.is_dir() and path.name not in wanted]


def _unneeded_npm(box: Path, needs: Needs) -> list[str]:
    wanted = {tarball_name(n.name, n.version) for n in needs.npm}
    return [f"npm {name}" for name in _names_in(box / "npm", "*.tgz") if name not in wanted]


def _unneeded_files(box: Path, needs: Needs) -> list[str]:
    wanted = {(f.name, f.version) for f in needs.files}
    folder = box / "files"
    if not folder.exists():
        return []
    return [
        f"file {name_dir.name} {version_dir.name}"
        for name_dir in folder.iterdir()
        if name_dir.is_dir()
        for version_dir in name_dir.iterdir()
        if (name_dir.name, version_dir.name) not in wanted
    ]
