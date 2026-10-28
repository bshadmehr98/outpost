"""What your projects need. One YAML file per project in needs/, merged into one list.

name: my-api
python: "3.12"                 # which Python the wheels are for
images: [python:3.12-slim, postgres:16]
pypi:
  - file: requirements.txt     # or poetry.lock, uv.lock, Pipfile.lock
  - httpx==0.27.0              # or a plain pip spec
npm:
  - file: package-lock.json    # or pnpm-lock.yaml, yarn.lock
  - chalk@5.3.0                # exact name@version (dependencies are not followed)
apt:
  distro: bookworm             # must match the image or host that will install them
  packages: [libpq-dev, curl]
apk:
  branch: v3.20                # Alpine branch of the image or host (alpine:3.20 -> v3.20)
  packages: [curl]
rpm:
  distro: rocky9               # rocky9 | alma9 | fedora40 | stream9
  packages: [curl]
files:
  - url: https://github.com/x/y/releases/download/v1.0/tool
    name: tool
    version: "1.0"
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import ConfigError
from . import parsers


@dataclass(frozen=True)
class FileNeed:
    name: str
    version: str
    url: str
    filename: str


@dataclass(frozen=True)
class NpmNeed:
    name: str
    version: str
    resolved: str | None = None  # tarball URL from a lockfile, if known
    integrity: str | None = None  # sha512-... from a lockfile, if known


@dataclass
class Needs:
    images: list[str] = field(default_factory=list)
    pypi: dict[str, list[str]] = field(default_factory=dict)  # python version -> pip specs
    npm: list[NpmNeed] = field(default_factory=list)
    apt: dict[str, list[str]] = field(default_factory=dict)  # distro -> packages
    apk: dict[str, list[str]] = field(default_factory=dict)  # alpine branch (v3.20) -> packages
    rpm: dict[str, list[str]] = field(default_factory=dict)  # rpm distro (rocky9) -> packages
    files: list[FileNeed] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # which needs files contributed

    def summary(self) -> dict[str, int]:
        return {
            "images": len(self.images),
            "pypi": sum(len(v) for v in self.pypi.values()),
            "npm": len(self.npm),
            "apt": sum(len(v) for v in self.apt.values()),
            "apk": sum(len(v) for v in self.apk.values()),
            "rpm": sum(len(v) for v in self.rpm.values()),
            "files": len(self.files),
        }

    def is_empty(self) -> bool:
        return not any(self.summary().values())


def _add_unique(target: list, items) -> None:
    for item in items:
        if item not in target:
            target.append(item)


def _parse_one(path: Path, needs: Needs) -> None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    base = path.parent
    python = str(data.get("python", "3.12"))

    _add_unique(needs.images, [str(i) for i in data.get("images") or []])

    specs = needs.pypi.setdefault(python, [])
    for entry in data.get("pypi") or []:
        if isinstance(entry, dict) and "file" in entry:
            _add_unique(specs, parsers.parse_python_file(base / entry["file"]))
        elif isinstance(entry, str):
            _add_unique(specs, [entry.strip()])
        else:
            raise ConfigError(f"{path}: pypi entries are pip specs or {{file: ...}} (got {entry!r})")
    if not specs:
        del needs.pypi[python]

    for entry in data.get("npm") or []:
        if isinstance(entry, dict) and "file" in entry:
            _add_unique(needs.npm, parsers.parse_js_file(base / entry["file"]))
        elif isinstance(entry, str) and "@" in entry.lstrip("@"):
            name, _, version = entry.rpartition("@")
            _add_unique(needs.npm, [NpmNeed(name=name, version=version)])
        else:
            raise ConfigError(f"{path}: npm entries are name@version or {{file: ...}} (got {entry!r})")

    # OS packages: one block or a list of blocks, keyed by the distro (apt, rpm) or the branch (apk)
    for kind, key, target in (("apt", "distro", needs.apt), ("apk", "branch", needs.apk), ("rpm", "distro", needs.rpm)):
        blocks = data.get(kind) or []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if not isinstance(block, dict) or not block.get(key):
                raise ConfigError(f"{path}: {kind} needs `{key}:` and `packages:`")
            pkgs = target.setdefault(str(block[key]), [])
            _add_unique(pkgs, [str(p) for p in block.get("packages") or []])

    for entry in data.get("files") or []:
        if not isinstance(entry, dict) or not entry.get("url") or not entry.get("name"):
            raise ConfigError(f"{path}: files need `url:` and `name:` (and usually `version:`)")
        url = str(entry["url"])
        _add_unique(
            needs.files,
            [
                FileNeed(
                    name=str(entry["name"]),
                    version=str(entry.get("version", "latest")),
                    url=url,
                    filename=str(entry.get("filename") or url.rstrip("/").rsplit("/", 1)[-1] or entry["name"]),
                )
            ],
        )
    needs.sources.append(path.name)


def load_needs(needs_dir: Path) -> Needs:
    """Merge every needs/*.yml into one Needs. Missing folder means nothing is needed."""
    needs = Needs()
    if not needs_dir.exists():
        return needs
    for path in sorted(needs_dir.glob("*.yml")) + sorted(needs_dir.glob("*.yaml")):
        _parse_one(path, needs)
    return needs


def add_platform_files(needs: Needs, platform_files: list[dict]) -> Needs:
    """Append the pinned platform downloads (gitea, docker, k3s...) to the files list."""
    _add_unique(needs.files, [FileNeed(**f) for f in platform_files])
    return needs
