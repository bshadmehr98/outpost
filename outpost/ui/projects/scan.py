"""scan(): look at a project folder and propose a needs file from what is there."""

import os
import re
import tomllib
from pathlib import Path

from outpost.config import ConfigError
from outpost.distros import kind_for_distro
from outpost.ui.projects.dockerfiles import ALPINE_CURRENT, distro_for_image, read_dockerfile
from outpost.ui.projects.lockfiles import JS_LOCKFILES, PYTHON_LOCKFILES, describe_lockfile

SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    "site-packages",
}
MAX_DEPTH = 3


def scan(folder: str | Path, gitea: str | None = None) -> dict:
    """Everything a needs file could be made of, found in a folder. The user picks what to keep.

    Args:
        gitea: `<name>/<org>` of this workspace's Gitea, when known (see needed_image).

    Raises:
        ConfigError: folder is not a folder on this laptop.
    """
    root = Path(os.path.expanduser(str(folder))).resolve()
    if not root.is_dir():
        raise ConfigError(f"{root} is not a folder on this computer")
    lockfiles, dockerfiles = _project_files(root, gitea)
    images = list(dict.fromkeys(image for d in dockerfiles for image in d["images"]))
    distros = _distro_of_each_family(images)
    packages = {
        kind: list(dict.fromkeys(p for d in dockerfiles for p in d.get(kind, []))) for kind in ("apt", "apk", "rpm")
    }
    proposal = {
        "folder": str(root),
        "name": re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "project",
        "python": python_version(root, images) or "3.12",
        "lockfiles": lockfiles,
        "dockerfiles": [d["path"] for d in dockerfiles],
        "images": images,
        "apt": {"distro": distros.get("apt") or "bookworm", "packages": packages["apt"]},
    }
    if packages["apk"] or distros.get("apk"):
        proposal["apk"] = {"branch": distros.get("apk") or ALPINE_CURRENT, "packages": packages["apk"]}
    if packages["rpm"] or distros.get("rpm"):
        proposal["rpm"] = {"distro": distros.get("rpm") or "rocky9", "packages": packages["rpm"]}
    return proposal


def python_version(folder: Path, images: list[str]) -> str | None:
    """The project's Python: .python-version, else pyproject's requires-python, else a python:X.Y image."""
    pv = folder / ".python-version"
    if pv.exists():
        m = re.match(r"\s*(\d+\.\d+)", pv.read_text())
        if m:
            return m.group(1)
    pyproject = folder / "pyproject.toml"
    if pyproject.exists():
        try:
            req = tomllib.loads(pyproject.read_text()).get("project", {}).get("requires-python", "")
            m = re.search(r"(\d+\.\d+)", req)
            if m:
                return m.group(1)
        except (tomllib.TOMLDecodeError, OSError):
            pass
    for image in images:
        m = re.match(r"(?:.*/)?python:(\d+\.\d+)", image)
        if m:
            return m.group(1)
    return None


def _project_files(root: Path, gitea: str | None) -> tuple[list[dict], list[dict]]:
    """The lockfiles and Dockerfiles in root, down to MAX_DEPTH folders, skipping SKIP_DIRS and hidden ones."""
    lockfiles: list[dict] = []
    dockerfiles: list[dict] = []
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")) if depth < MAX_DEPTH else []
        for name in sorted(files):
            path = Path(current) / name
            if name in PYTHON_LOCKFILES or name in JS_LOCKFILES or re.fullmatch(r"requirements[-_\w]*\.txt", name):
                lockfiles.append(describe_lockfile(path))
            elif name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile"):
                dockerfiles.append(read_dockerfile(path, gitea))
    return lockfiles, dockerfiles


def _distro_of_each_family(images: list[str]) -> dict:
    """{kind: distro}: the first image of each family names the distro for that family's packages."""
    distros = {}
    for image in images:
        distro = distro_for_image(image)
        distros.setdefault(kind_for_distro(distro), distro)
    return distros
