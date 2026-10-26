"""Turning a project folder, or a single lockfile, into a needs file. Used by the dashboard.

  scan(folder)                    look at a folder: lockfiles, Dockerfiles, Python version -> a proposal
  describe_lockfile(path)         what one lockfile is and how many packages it pins
  write_needs(platform, project)  write needs/<name>.yml from a proposal the user accepted

Nothing here is clever. It finds the files you already have and reads them with the same parsers
`outpost fetch` uses, so what the page shows is what will be fetched.
"""

import os
import re
import tomllib
from pathlib import Path

import yaml

from ..config import ConfigError
from ..needs import parsers
from ..paths import Platform

PYTHON_LOCKFILES = ("requirements.txt", "requirements.in", "poetry.lock", "uv.lock", "Pipfile.lock")
JS_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "dist", "build", ".tox", ".mypy_cache", "site-packages"}
MAX_DEPTH = 3

# base image -> apt distro, for the usual suspects
_CODENAMES = ("bookworm", "bullseye", "trixie", "noble", "jammy", "focal")
_UBUNTU = {"24.04": "noble", "22.04": "jammy", "20.04": "focal"}


def describe_lockfile(path: Path) -> dict:
    """{"path", "kind": "python"|"js"|None, "count", "error"} for one file."""
    info = {"path": str(path), "name": path.name, "kind": None, "count": 0, "error": None}
    try:
        if path.name in JS_LOCKFILES:
            info["kind"] = "js"
            info["count"] = len(parsers.parse_js_file(path))
        elif path.name in PYTHON_LOCKFILES or path.suffix in (".txt", ".in"):
            info["kind"] = "python"
            info["count"] = len(parsers.parse_python_file(path))
        else:
            info["error"] = "not a lockfile I can read: " + ", ".join(PYTHON_LOCKFILES + JS_LOCKFILES)
    except (ConfigError, OSError, ValueError) as e:
        info["error"] = str(e)
    return info


def distro_for_image(image: str) -> str | None:
    """`python:3.12-slim` -> bookworm, `ubuntu:24.04` -> noble, unknown -> None."""
    name, _, tag = image.partition(":")
    name = name.rsplit("/", 1)[-1]
    for codename in _CODENAMES:
        if codename in tag or (name == "debian" and tag.startswith(codename)):
            return codename
    if name == "ubuntu":
        return _UBUNTU.get(tag.split("-")[0]) or (tag if tag.isalpha() else None)
    if name in ("python", "node", "debian", "postgres", "nginx", "redis") and "alpine" not in tag:
        return "bookworm"  # Debian's current stable, which these official images build on
    return None


def read_dockerfile(path: Path) -> dict:
    """Images and apt packages a Dockerfile asks for. Build stages and ${ARGS} are left out."""
    text = path.read_text(errors="replace")
    stages: set[str] = set()
    images: list[str] = []
    for m in re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", text, re.I | re.M):
        image, stage = m.group(1), m.group(2)
        if stage:
            stages.add(stage.lower())
        if image.lower() in stages or image.lower() == "scratch":
            continue
        if "$" in image:  # ${REGISTRY}/${ORG}/python:3.12-slim -> python:3.12-slim, if the tail stands on its own
            tail = image.rsplit("/", 1)[-1]
            if "$" in tail or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[A-Za-z0-9._-]+", tail):
                continue
            image = tail
        if image not in images:
            images.append(image)
    packages: list[str] = []
    joined = re.sub(r"\\\s*\n", " ", text)
    for m in re.finditer(r"apt-get\s+(?:-\S+\s+)*install\s+(.*?)(?:&&|;|\n|$)", joined):
        for word in m.group(1).split():
            if word.startswith("-") or word.split("=")[0].isupper():  # a flag, or VAR=value
                continue
            word = word.split("=")[0]  # package=version pins the version; we fetch the distro's current one
            if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", word) and word not in packages:
                packages.append(word)
    return {"path": str(path), "images": images, "apt": packages}


def python_version(folder: Path, images: list[str]) -> str | None:
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


def scan(folder: str | Path) -> dict:
    """Everything a needs file could be made of, found in a folder. The user picks what to keep."""
    root = Path(os.path.expanduser(str(folder))).resolve()
    if not root.is_dir():
        raise ConfigError(f"{root} is not a folder on this computer")
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
                dockerfiles.append(read_dockerfile(path))
    images = [i for d in dockerfiles for i in d["images"]]
    images = list(dict.fromkeys(images))
    apt = list(dict.fromkeys(p for d in dockerfiles for p in d["apt"]))
    distro = next((distro_for_image(i) for i in images if distro_for_image(i)), None)
    return {
        "folder": str(root),
        "name": re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "project",
        "python": python_version(root, images) or "3.12",
        "lockfiles": lockfiles,
        "dockerfiles": [d["path"] for d in dockerfiles],
        "images": images,
        "apt": {"distro": distro or "bookworm", "packages": apt},
    }


def write_needs(platform: Platform, project: dict) -> Path:
    """Write needs/<name>.yml. Lockfile paths are stored relative to needs/, as `outpost fetch` reads them."""
    name = re.sub(r"[^a-z0-9._-]+", "-", str(project.get("name", "")).lower()).strip("-.")
    if not name:
        raise ConfigError("the project needs a name")
    needs_dir = platform.needs_dir
    needs_dir.mkdir(parents=True, exist_ok=True)

    def rel(p: str) -> str:
        return os.path.relpath(Path(os.path.expanduser(p)).resolve(), needs_dir)

    pypi = [{"file": rel(p)} for p in project.get("pypi_files") or []] + [s.strip() for s in project.get("pypi") or [] if s.strip()]
    npm = [{"file": rel(p)} for p in project.get("npm_files") or []] + [s.strip() for s in project.get("npm") or [] if s.strip()]
    data: dict = {"name": name, "python": str(project.get("python") or "3.12")}
    if project.get("folder"):
        data["folder"] = str(project["folder"])  # where it was scanned from, so the dashboard can scan again
    if project.get("images"):
        data["images"] = [i.strip() for i in project["images"] if i.strip()]
    if pypi:
        data["pypi"] = pypi
    if npm:
        data["npm"] = npm
    apt = project.get("apt") or {}
    if apt.get("packages"):
        data["apt"] = {"distro": str(apt.get("distro") or "bookworm"), "packages": [p.strip() for p in apt["packages"] if p.strip()]}
    if project.get("files"):
        data["files"] = project["files"]
    path = needs_dir / f"{name}.yml"
    path.write_text("# Written by the Outpost dashboard. Edit freely; see docs/reference/needs.md.\n"
                    + yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100))
    return path
