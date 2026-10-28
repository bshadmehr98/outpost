"""Turn lockfiles you already have into flat lists. One small function per format.

Python:  requirements.txt, poetry.lock, uv.lock, Pipfile.lock  -> ["name==version", ...]
JS:      package-lock.json, pnpm-lock.yaml, yarn.lock           -> [NpmNeed, ...]

Adding a format means adding a function here and one line in the dispatch table.
"""

import json
import re
import tomllib
from pathlib import Path

import yaml

from ..config import ConfigError

# -- Python ------------------------------------------------------------------


def parse_requirements(path: Path, _seen: set | None = None) -> list[str]:
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
            continue  # URLs and local paths cannot be mirrored; list them under files:
        specs.append(line)
    return specs


def parse_poetry_lock(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text())
    return [
        f"{p['name']}=={p['version']}"
        for p in data.get("package", [])
        if p.get("source", {}).get("type") not in ("directory", "file", "git", "url")
    ]


def parse_uv_lock(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text())
    specs = []
    for p in data.get("package", []):
        source = p.get("source", {})
        if "registry" not in source:
            continue  # the project itself, editable paths, git sources
        specs.append(f"{p['name']}=={p['version']}")
    return specs


def parse_pipfile_lock(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    specs = []
    for section in ("default", "develop"):
        for name, spec in (data.get(section) or {}).items():
            version = spec.get("version") if isinstance(spec, dict) else None
            if version:
                specs.append(f"{name}{version}")  # Pipfile.lock stores "==1.2.3"
    return specs


def parse_python_file(path: Path) -> list[str]:
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


# -- JavaScript --------------------------------------------------------------


def parse_package_lock(path: Path) -> list:
    from . import NpmNeed

    data = json.loads(path.read_text())
    needs: list[NpmNeed] = []
    if "packages" in data:  # lockfileVersion 2 and 3
        for key, meta in data["packages"].items():
            if not key or meta.get("link") or not meta.get("version") or "node_modules/" not in key:
                continue
            name = meta.get("name") or key.rsplit("node_modules/", 1)[-1]
            needs.append(
                NpmNeed(
                    name=name, version=meta["version"], resolved=meta.get("resolved"), integrity=meta.get("integrity")
                )
            )
    else:  # lockfileVersion 1

        def walk(deps: dict):
            for name, meta in deps.items():
                if meta.get("version") and not str(meta.get("version")).startswith("file:"):
                    needs.append(
                        NpmNeed(
                            name=name,
                            version=meta["version"],
                            resolved=meta.get("resolved"),
                            integrity=meta.get("integrity"),
                        )
                    )
                walk(meta.get("dependencies") or {})

        walk(data.get("dependencies") or {})
    return _dedupe(needs)


_PNPM_KEY = re.compile(r"^/?(?P<name>@?[^@/]+(?:/[^@/]+)?)@(?P<version>[^(]+)")


def parse_pnpm_lock(path: Path) -> list:
    from . import NpmNeed

    data = yaml.safe_load(path.read_text()) or {}
    needs: list[NpmNeed] = []
    for key, meta in (data.get("packages") or {}).items():
        m = _PNPM_KEY.match(str(key))
        if not m:
            continue
        resolution = (meta or {}).get("resolution") or {}
        if "tarball" in resolution and not resolution["tarball"].startswith("http"):
            continue
        needs.append(
            NpmNeed(
                name=m["name"],
                version=m["version"].strip(),
                resolved=resolution.get("tarball"),
                integrity=resolution.get("integrity"),
            )
        )
    return _dedupe(needs)


_YARN_HEADER = re.compile(r'^"?(@?[^@"]+)@')


def parse_yarn_lock(path: Path) -> list:
    """yarn.lock v1 (classic). Blocks look like:  name@^1.0.0:\\n  version "1.2.3"\\n  resolved "url#sha"\\n  integrity sha512-..."""
    from . import NpmNeed

    needs: list[NpmNeed] = []
    name = version = resolved = integrity = None
    lines = path.read_text().splitlines() + [""]
    for line in lines:
        if line and not line.startswith((" ", "\t", "#")):
            m = _YARN_HEADER.match(line)
            name = m.group(1) if m else None
            version = resolved = integrity = None
        elif line.strip().startswith("version "):
            version = line.split(None, 1)[1].strip().strip('"')
        elif line.strip().startswith("resolved "):
            resolved = line.split(None, 1)[1].strip().strip('"').split("#", 1)[0]
        elif line.strip().startswith("integrity "):
            integrity = line.split(None, 1)[1].strip().strip('"')
        elif not line.strip() and name and version:
            needs.append(NpmNeed(name=name, version=version, resolved=resolved, integrity=integrity))
            name = version = None
    return _dedupe(needs)


def parse_js_file(path: Path) -> list:
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


def _dedupe(items: list) -> list:
    seen = set()
    out = []
    for item in items:
        key = (item.name, item.version)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
