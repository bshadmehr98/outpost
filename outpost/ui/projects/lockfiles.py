"""describe_lockfile(): what one lockfile is and how many packages it pins, read with fetch's own parsers."""

from pathlib import Path

from outpost.config import ConfigError
from outpost.needs import read_js_lockfile, read_python_lockfile

PYTHON_LOCKFILES = ("requirements.txt", "requirements.in", "poetry.lock", "uv.lock", "Pipfile.lock")
JS_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")


def describe_lockfile(path: Path) -> dict:
    """{"path", "kind": "python"|"js"|None, "count", "error"} for one file."""
    info = {"path": str(path), "name": path.name, "kind": None, "count": 0, "error": None}
    try:
        if path.name in JS_LOCKFILES:
            info["kind"] = "js"
            info["count"] = len(read_js_lockfile(path))
        elif path.name in PYTHON_LOCKFILES or path.suffix in (".txt", ".in"):
            info["kind"] = "python"
            info["count"] = len(read_python_lockfile(path))
        else:
            info["error"] = "not a lockfile I can read: " + ", ".join(PYTHON_LOCKFILES + JS_LOCKFILES)
    except (ConfigError, OSError, ValueError) as e:
        info["error"] = str(e)
    return info
