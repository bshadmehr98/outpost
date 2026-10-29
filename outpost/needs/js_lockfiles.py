"""JavaScript lockfiles you already have, read into npm packages: [NpmNeed, ...].

    package-lock.json, npm-shrinkwrap.json   lockfileVersion 1, 2 and 3
    pnpm-lock.yaml                           packages: with their resolution
    yarn.lock                                yarn v1 (classic)

Each package keeps its tarball URL and integrity when the lockfile has them, so fetch can check
what it downloads. Adding a format means adding a function here and one line in read_js_lockfile.
"""

import json
import re
from pathlib import Path

import yaml

from outpost.config import ConfigError
from outpost.needs.model import NpmNeed

_PNPM_KEY = re.compile(r"^/?(?P<name>@?[^@/]+(?:/[^@/]+)?)@(?P<version>[^(]+)")
_YARN_HEADER = re.compile(r'^"?(@?[^@"]+)@')


def read_js_lockfile(path: Path) -> list[NpmNeed]:
    """The npm packages in one JavaScript lockfile, picked by its name.

    Raises:
        ConfigError: the file is missing, or is not a format Outpost reads.
    """
    if not path.exists():
        raise ConfigError(f"needs file points to {path}, which does not exist")
    name = path.name
    if name == "package-lock.json" or name == "npm-shrinkwrap.json":
        return parse_package_lock(path)
    if name == "pnpm-lock.yaml":
        return parse_pnpm_lock(path)
    if name == "yarn.lock":
        return parse_yarn_lock(path)
    raise ConfigError(f"Don't know how to read {name}. Supported: package-lock.json, pnpm-lock.yaml, yarn.lock")


def parse_package_lock(path: Path) -> list[NpmNeed]:
    """Every installed package in a package-lock.json (links and the project's own workspaces left out)."""
    data = json.loads(path.read_text())
    if "packages" in data:
        needs = _packages_of_lockfile_v2(data["packages"])
    else:
        needs = _dependencies_of_lockfile_v1(data.get("dependencies") or {})
    return _dedupe(needs)


def _packages_of_lockfile_v2(packages: dict) -> list[NpmNeed]:
    """lockfileVersion 2 and 3: a flat map of node_modules/... paths."""
    needs = []
    for key, meta in packages.items():
        if not key or meta.get("link") or not meta.get("version") or "node_modules/" not in key:
            continue
        name = meta.get("name") or key.rsplit("node_modules/", 1)[-1]
        needs.append(_npm_need(name, meta))
    return needs


def _dependencies_of_lockfile_v1(dependencies: dict) -> list[NpmNeed]:
    """lockfileVersion 1: a tree of dependencies, each with its own dependencies."""
    needs = []
    for name, meta in dependencies.items():
        if meta.get("version") and not str(meta.get("version")).startswith("file:"):
            needs.append(_npm_need(name, meta))
        needs += _dependencies_of_lockfile_v1(meta.get("dependencies") or {})
    return needs


def _npm_need(name: str, meta: dict) -> NpmNeed:
    return NpmNeed(name=name, version=meta["version"], resolved=meta.get("resolved"), integrity=meta.get("integrity"))


def parse_pnpm_lock(path: Path) -> list[NpmNeed]:
    """Every package in a pnpm-lock.yaml; local tarballs (a resolution that is not a URL) are left out."""
    data = yaml.safe_load(path.read_text()) or {}
    needs = []
    for key, meta in (data.get("packages") or {}).items():
        match = _PNPM_KEY.match(str(key))
        if not match:
            continue
        resolution = (meta or {}).get("resolution") or {}
        if "tarball" in resolution and not resolution["tarball"].startswith("http"):
            continue
        needs.append(
            NpmNeed(
                name=match["name"],
                version=match["version"].strip(),
                resolved=resolution.get("tarball"),
                integrity=resolution.get("integrity"),
            )
        )
    return _dedupe(needs)


def parse_yarn_lock(path: Path) -> list[NpmNeed]:
    """Every package in a yarn.lock v1 (classic).

    Blocks look like:  name@^1.0.0:\\n  version "1.2.3"\\n  resolved "url#sha"\\n  integrity sha512-...
    """
    needs = []
    name = version = resolved = integrity = None
    for line in path.read_text().splitlines() + [""]:
        if line and not line.startswith((" ", "\t", "#")):
            match = _YARN_HEADER.match(line)
            name = match.group(1) if match else None
            version = resolved = integrity = None
        elif line.strip().startswith("version "):
            version = _yarn_value(line)
        elif line.strip().startswith("resolved "):
            resolved = _yarn_value(line).split("#", 1)[0]
        elif line.strip().startswith("integrity "):
            integrity = _yarn_value(line)
        elif not line.strip() and name and version:
            needs.append(NpmNeed(name=name, version=version, resolved=resolved, integrity=integrity))
            name = version = None
    return _dedupe(needs)


def _yarn_value(line: str) -> str:
    """`  version "1.2.3"` -> 1.2.3."""
    return line.split(None, 1)[1].strip().strip('"')


def _dedupe(needs: list[NpmNeed]) -> list[NpmNeed]:
    """One NpmNeed per name and version, the first one kept."""
    seen = set()
    out = []
    for need in needs:
        key = (need.name, need.version)
        if key not in seen:
            seen.add(key)
            out.append(need)
    return out
