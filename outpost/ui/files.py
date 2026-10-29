"""The files the dashboard may read and write: servers.yml, mirrors.yml, needs/*.yml, checks/*.yml,
and lockfiles uploaded into needs/<project>/."""

import re
from pathlib import Path

import yaml

from outpost.ui.projects import describe_lockfile
from outpost.workspace import Workspace


def editable_file(workspace: Workspace, name: str) -> Path:
    """The path of an editable file, from its name relative to the workspace.

    Raises:
        PermissionError: name is not servers.yml, mirrors.yml, or a .yml in needs/ or checks/.
    """
    path = (workspace.root / name).resolve()
    allowed = (
        path == workspace.servers_file
        or path == workspace.mirrors_file
        or (path.parent in (workspace.needs_dir, workspace.checks_dir) and path.suffix in (".yml", ".yaml"))
    )
    if not allowed or ".." in name:
        raise PermissionError(f"not an editable file: {name}")
    return path


def read_file(workspace: Workspace, name: str) -> dict:
    """{"name", "content"}; the content is "" when the file does not exist yet."""
    path = editable_file(workspace, name)
    return {"name": name, "content": path.read_text() if path.exists() else ""}


def save_file(workspace: Workspace, name: str, content: str) -> dict:
    """Write an editable file. It must at least be valid YAML.

    Raises:
        PermissionError: not an editable file.
        yaml.YAMLError: content is not YAML; the file is left as it was.
    """
    path = editable_file(workspace, name)
    yaml.safe_load(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {"saved": name}


def delete_needs_file(workspace: Workspace, name: str) -> dict:
    """Remove one needs file. Other editable files cannot be deleted here.

    Raises:
        PermissionError: name is not a needs file.
    """
    path = editable_file(workspace, name)
    if path.parent != workspace.needs_dir:
        raise PermissionError("only needs files can be deleted here")
    path.unlink(missing_ok=True)
    return {"deleted": name}


def save_lockfile(workspace: Workspace, project: str, filename: str, body: bytes) -> dict:
    """Store an uploaded lockfile as needs/<project>/<filename>, and say what it pins (describe_lockfile).

    Raises:
        PermissionError: the project or the file name is missing, or the name starts with a dot.
    """
    project = re.sub(r"[^a-z0-9._-]+", "-", project.lower()).strip("-.")
    filename = Path(filename).name
    if not project or not filename or filename.startswith("."):
        raise PermissionError("the upload needs a project name and a file name")
    folder = workspace.needs_dir / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(body)
    return describe_lockfile(path)
