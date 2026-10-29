"""The demo project's files on the laptop: demo/ and needs/demo.yml in the workspace."""

import hashlib
import shutil
from pathlib import Path

from outpost.workspace import TEMPLATE_DIR, Workspace

NAME = "demo"  # the folder in the workspace, and the repository in Gitea
TEMPLATE = TEMPLATE_DIR / NAME
IGNORE = {".git", "__pycache__", ".gitea"}  # never committed from demo/

NEEDS_YML = """\
# The demo project in demo/. Delete this file and the folder once you have your own projects.
name: demo
python: "3.12"
images: [python:3.12-slim]
pypi:
  - file: ../demo/requirements.txt
apt:
  distro: bookworm
  packages: [curl]
"""


def ensure_local(workspace: Workspace) -> Path:
    """demo/ and needs/demo.yml exist in the workspace (older workspaces get them now). Returns demo/."""
    folder = workspace.demo_dir
    if not folder.exists():
        shutil.copytree(TEMPLATE, folder)
    needs = workspace.needs_dir / f"{NAME}.yml"
    if not needs.exists():
        needs.write_text(NEEDS_YML)
    return folder


def project_files(folder: Path) -> dict[str, bytes]:
    """Relative path -> content, for everything in demo/ except caches and the generated workflow."""
    files = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if path.is_file() and not (set(rel.parts) & IGNORE):
            files[rel.as_posix()] = path.read_bytes()
    return files


def git_blob_sha(content: bytes) -> str:
    """The sha git gives a file with this content, so it can be compared with what Gitea has."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
