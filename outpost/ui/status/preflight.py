"""preflight(): what this laptop needs (ssh, openssl, docker, pip, free disk), and which step cares."""

import shutil
import subprocess

from outpost.fetch.pypi import pip_command
from outpost.workspace import Workspace

# (tool, the steps that need it, why), checked with `which`
TOOLS = (
    ("ssh", "all server steps", "talks to the servers"),
    ("openssl", "gitea setup", "makes the CA and certificates"),
    ("docker", "fetch (apt, apk, rpm) and the lab", "resolves OS packages in a container"),
)
FREE_DISK_GB = 5  # below this, fetch and the lab are likely to run out


def preflight(workspace: Workspace) -> list[dict]:
    """One {"name", "ok", "detail", "need"} per thing this laptop needs; `need` says which step cares."""
    found = []
    for tool, need, why in TOOLS:
        path = shutil.which(tool)
        found.append({"name": tool, "ok": bool(path), "detail": path or f"not found; {why}", "need": need})
    disk = _free_disk(workspace)
    if disk:
        found.append(disk)
    found.append(_pip())
    return found


def _free_disk(workspace: Workspace) -> dict | None:
    try:
        usage = shutil.disk_usage(workspace.root)
    except OSError:
        return None
    free_gb = usage.free / 1e9
    return {
        "name": "free disk here",
        "ok": free_gb > FREE_DISK_GB,
        "detail": f"{free_gb:.0f} GB free of {usage.total / 1e9:.0f} GB",
        "need": "fetch and the lab",
    }


def _pip() -> dict:
    try:
        version = subprocess.run(pip_command() + ["--version"], capture_output=True, text=True).stdout.strip()
    except RuntimeError as e:
        return {"name": "pip", "ok": False, "detail": str(e), "need": "fetch (pypi)"}
    return {"name": "pip", "ok": True, "detail": version.split(" from ")[0][:40], "need": "fetch (pypi)"}
