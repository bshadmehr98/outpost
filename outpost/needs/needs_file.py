"""Reading needs files: needs/*.yml, one per project, merged into one Needs.

Each kind has its own small reader below. A lockfile named under pypi: or npm: is read by
python_lockfiles.py or js_lockfiles.py.
"""

from pathlib import Path

import yaml

from outpost.config import ConfigError
from outpost.needs.js_lockfiles import read_js_lockfile
from outpost.needs.model import FileNeed, Needs, NpmNeed, add_unique
from outpost.needs.python_lockfiles import read_python_lockfile

DEFAULT_PYTHON = "3.12"
# OS package kinds: the key that names the distro (or branch) a block of packages is for
OS_PACKAGE_KEYS = (("apt", "distro"), ("apk", "branch"), ("rpm", "distro"))


def load_needs(needs_dir: Path) -> Needs:
    """Merge every needs/*.yml (and *.yaml) into one Needs. A missing folder means nothing is needed.

    Raises:
        ConfigError: a needs file is not valid, or names a lockfile that is missing or unknown.
    """
    needs = Needs()
    if not needs_dir.exists():
        return needs
    for path in sorted(needs_dir.glob("*.yml")) + sorted(needs_dir.glob("*.yaml")):
        read_needs_file(path, needs)
    return needs


def read_needs_file(path: Path, needs: Needs) -> None:
    """Add what one needs file asks for to needs.

    Raises:
        ConfigError: the file is not valid YAML, not a mapping, or an entry is wrong.
    """
    data = _read_mapping(path)
    _read_images(data, needs)
    _read_pypi(path, data, needs)
    _read_npm(path, data, needs)
    _read_os_packages(path, data, needs)
    _read_files(path, data, needs)
    needs.sources.append(path.name)


def _read_mapping(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    return data


def _read_images(data: dict, needs: Needs) -> None:
    add_unique(needs.images, [str(image) for image in data.get("images") or []])


def _read_pypi(path: Path, data: dict, needs: Needs) -> None:
    """pip specs and Python lockfiles, under the Python version the wheels are for."""
    python = _python_version(path, data)
    specs = needs.pypi.setdefault(python, [])
    for entry in data.get("pypi") or []:
        if isinstance(entry, dict) and "file" in entry:
            add_unique(specs, read_python_lockfile(path.parent / entry["file"]))
        elif isinstance(entry, str):
            add_unique(specs, [entry.strip()])
        else:
            raise ConfigError(f"{path}: pypi entries are pip specs or {{file: ...}} (got {entry!r})")
    if not specs:
        del needs.pypi[python]


def _python_version(path: Path, data: dict) -> str:
    """`python:` as text. YAML reads an unquoted 3.10 as the number 3.1, which would ask pip for Python 3.1."""
    python = data.get("python", DEFAULT_PYTHON)
    if isinstance(python, float) and len(str(python).split(".")[1]) == 1:
        raise ConfigError(
            f'{path}: python: {python} is read as the number {python}; put the version in quotes, such as python: "{python}0"'
        )
    return str(python)


def _read_npm(path: Path, data: dict, needs: Needs) -> None:
    """name@version entries and JavaScript lockfiles."""
    for entry in data.get("npm") or []:
        if isinstance(entry, dict) and "file" in entry:
            add_unique(needs.npm, read_js_lockfile(path.parent / entry["file"]))
        elif isinstance(entry, str) and "@" in entry.lstrip("@"):
            name, _, version = entry.rpartition("@")
            add_unique(needs.npm, [NpmNeed(name=name, version=version)])
        else:
            raise ConfigError(f"{path}: npm entries are name@version or {{file: ...}} (got {entry!r})")


def _read_os_packages(path: Path, data: dict, needs: Needs) -> None:
    """apt, apk and rpm: one block or a list of blocks, each with its distro (or branch) and packages."""
    for kind, key in OS_PACKAGE_KEYS:
        blocks = data.get(kind) or []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if not isinstance(block, dict) or not block.get(key):
                raise ConfigError(f"{path}: {kind} needs `{key}:` and `packages:`")
            packages = getattr(needs, kind).setdefault(str(block[key]), [])
            add_unique(packages, [str(package) for package in block.get("packages") or []])


def _read_files(path: Path, data: dict, needs: Needs) -> None:
    """Plain downloads; the file name defaults to the last part of the URL."""
    for entry in data.get("files") or []:
        if not isinstance(entry, dict) or not entry.get("url") or not entry.get("name"):
            raise ConfigError(f"{path}: files need `url:` and `name:` (and usually `version:`)")
        url = str(entry["url"])
        file_need = FileNeed(
            name=str(entry["name"]),
            version=str(entry.get("version", "latest")),
            url=url,
            filename=str(entry.get("filename") or url.rstrip("/").rsplit("/", 1)[-1] or entry["name"]),
        )
        add_unique(needs.files, [file_need])
