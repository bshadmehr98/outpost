"""write_needs(): turn a proposal the user accepted into needs/<name>.yml."""

import os
import re
from pathlib import Path

import yaml

from outpost.config import ConfigError
from outpost.ui.projects.dockerfiles import ALPINE_CURRENT
from outpost.workspace import Workspace

HEADER = "# Written by the Outpost dashboard. Edit freely; see docs/reference/needs.md.\n"


def write_needs(workspace: Workspace, project: dict) -> Path:
    """Write needs/<name>.yml and return its path. Lockfile paths are stored relative to needs/, as
    `outpost fetch` reads them.

    Raises:
        ConfigError: the project has no name.
    """
    name = re.sub(r"[^a-z0-9._-]+", "-", str(project.get("name", "")).lower()).strip("-.")
    if not name:
        raise ConfigError("the project needs a name")
    needs_dir = workspace.needs_dir
    needs_dir.mkdir(parents=True, exist_ok=True)
    path = needs_dir / f"{name}.yml"
    data = _needs_file(name, project, needs_dir)
    path.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100))
    return path


def _needs_file(name: str, project: dict, needs_dir: Path) -> dict:
    """The needs file's content, in the order a person would write it; empty sections are left out."""
    data: dict = {"name": name, "python": str(project.get("python") or "3.12")}
    if project.get("folder"):
        data["folder"] = str(project["folder"])  # where it was scanned from, so the dashboard can scan again
    if project.get("images"):
        data["images"] = _stripped(project["images"])
    for kind in ("pypi", "npm"):
        entries = _package_entries(project, kind, needs_dir)
        if entries:
            data[kind] = entries
    for kind, key, default in (
        ("apt", "distro", "bookworm"),
        ("apk", "branch", ALPINE_CURRENT),
        ("rpm", "distro", "rocky9"),
    ):
        section = project.get(kind) or {}
        if section.get("packages"):
            data[kind] = {key: str(section.get(key) or default), "packages": _stripped(section["packages"])}
    if project.get("files"):
        data["files"] = project["files"]
    return data


def _package_entries(project: dict, kind: str, needs_dir: Path) -> list:
    """For pypi or npm: one {"file": ...} per lockfile (relative to needs/), then the plain specs."""
    files = [{"file": _relative_to(needs_dir, path)} for path in project.get(f"{kind}_files") or []]
    return files + _stripped(project.get(kind) or [])


def _relative_to(needs_dir: Path, path: str) -> str:
    return os.path.relpath(Path(os.path.expanduser(path)).resolve(), needs_dir)


def _stripped(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]
