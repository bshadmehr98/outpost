"""Python lockfiles you already have, read into flat pip specs: ["name==version", ...].

    requirements*.txt, *.in   pip's own format, with -r includes
    poetry.lock, uv.lock      TOML; only packages from a registry
    Pipfile.lock              JSON; default and develop

Adding a format means adding a function here and one line in read_python_lockfile.
"""

import json
import tomllib
from pathlib import Path

from outpost.config import ConfigError

NOT_FROM_A_REGISTRY = ("directory", "file", "git", "url")  # poetry source types that cannot be mirrored


def read_python_lockfile(path: Path) -> list[str]:
    """The pip specs in one Python lockfile, picked by its name.

    Raises:
        ConfigError: the file is missing, or is not a format Outpost reads.
    """
    if not path.exists():
        raise ConfigError(f"needs file points to {path}, which does not exist")
    name = path.name
    if name == "poetry.lock":
        return parse_poetry_lock(path)
    if name == "uv.lock":
        return parse_uv_lock(path)
    if name == "Pipfile.lock":
        return parse_pipfile_lock(path)
    if name.endswith(".txt") or name.endswith(".in"):
        return parse_requirements(path)
    raise ConfigError(
        f"Don't know how to read {name}. Supported: requirements*.txt, poetry.lock, uv.lock, Pipfile.lock"
    )


def parse_requirements(path: Path, _seen: set | None = None) -> list[str]:
    """pip specs from a requirements file, following `-r` includes once each.

    pip options, URLs and local paths are left out: they cannot be mirrored (list them under files:).
    """
    seen = _seen if _seen is not None else set()
    if path.resolve() in seen:
        return []
    seen.add(path.resolve())
    specs: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            specs += parse_requirements(path.parent / line.split(None, 1)[1].strip(), seen)
            continue
        if line.startswith("-"):
            continue  # pip options like --index-url do not belong in the needs list
        line = line.split(" \\")[0].strip()
        if line.startswith(("http://", "https://", "git+", "./", "/")):
            continue
        specs.append(line)
    return specs


def parse_poetry_lock(path: Path) -> list[str]:
    """name==version for every poetry.lock package that comes from a registry."""
    data = tomllib.loads(path.read_text())
    return [
        f"{p['name']}=={p['version']}"
        for p in data.get("package", [])
        if p.get("source", {}).get("type") not in NOT_FROM_A_REGISTRY
    ]


def parse_uv_lock(path: Path) -> list[str]:
    """name==version for every uv.lock package that comes from a registry (not the project itself, paths or git)."""
    data = tomllib.loads(path.read_text())
    return [f"{p['name']}=={p['version']}" for p in data.get("package", []) if "registry" in p.get("source", {})]


def parse_pipfile_lock(path: Path) -> list[str]:
    """name==version for the default and develop packages of a Pipfile.lock."""
    data = json.loads(path.read_text())
    specs = []
    for section in ("default", "develop"):
        for name, spec in (data.get(section) or {}).items():
            version = spec.get("version") if isinstance(spec, dict) else None
            if version:
                specs.append(f"{name}{version}")  # Pipfile.lock stores "==1.2.3"
    return specs
