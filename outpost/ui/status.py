"""What the dashboard's overview is made of: is the platform in sync, what is missing, is this laptop ready.

  box_status(platform, needs)   per kind: how much is in the box, and which needs are not there yet
  sync(platform, box)           needs -> box -> Gitea -> nodes: which arrow is stale and why
  preflight(platform)           docker, ssh, openssl, free disk on this computer
  runs(platform)                past dashboard runs, newest first, with their exit code and a one-line summary
  search(platform, needs, q)    find a package or image in the needs and in the box
  steps(log)                    the "==> " progress lines of a run, for the narrated progress panel
  box_changes(platform, needs)  what is new in the box since the last upload, and what is in the box but not needed

Everything is read from files; nothing here talks to a server.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .. import oci
from ..box import KINDS
from ..fetch.npm import tarball_name
from ..needs import Needs
from ..paths import Platform
from . import explain


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _spec_parts(spec: str) -> tuple[str, str | None]:
    """'Flask==3.1.0' -> ('flask', '3.1.0'); 'requests>=2' -> ('requests', None)."""
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:==\s*([^\s;,]+))?", spec)
    return (_norm(m.group(1)), m.group(2)) if m else (_norm(spec), None)


def box_status(platform: Platform, needs: Needs) -> dict:
    """{"kinds": {kind: {files, bytes}}, "missing": {kind: [...]}, "missing_total": n}"""
    box = platform.box_dir
    kinds = {k: {"files": 0, "bytes": 0} for k in KINDS}
    for kind in KINDS:
        root = box / kind
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    kinds[kind]["files"] += 1
                    kinds[kind]["bytes"] += p.stat().st_size

    missing: dict[str, list[str]] = {k: [] for k in KINDS}
    for ref in needs.images:
        try:
            if not (box / "images" / oci.parse_ref(ref).safe_name / "index.json").exists():
                missing["images"].append(ref)
        except ValueError:
            missing["images"].append(ref)
    pypi_files = [p.name.lower() for p in (box / "pypi").iterdir()] if (box / "pypi").exists() else []
    for specs in needs.pypi.values():
        for spec in specs:
            name, version = _spec_parts(spec)
            prefix = _norm(name + "-" + (version.lower() if version else ""))
            if not any(_norm(f).startswith(prefix) for f in pypi_files):
                missing["pypi"].append(spec)
    for n in needs.npm:
        if not (box / "npm" / tarball_name(n.name, n.version)).exists():
            missing["npm"].append(f"{n.name}@{n.version}")
    for distro, pkgs in needs.apt.items():
        debs = [p.name for p in (box / "apt" / distro).glob("*.deb")] if (box / "apt" / distro).exists() else []
        for pkg in pkgs:
            if not any(d.startswith(pkg + "_") for d in debs):
                missing["apt"].append(f"{pkg} ({distro})")
    for kind, wanted, ext in (("apk", needs.apk, "*.apk"), ("rpm", needs.rpm, "*.rpm")):  # name-version-... files
        for distro, pkgs in wanted.items():
            have = [p.name for p in (box / kind / distro).glob(ext)] if (box / kind / distro).exists() else []
            for pkg in pkgs:
                if not any(f.startswith(pkg + "-") for f in have):
                    missing[kind].append(f"{pkg} ({distro})")
    for f in needs.files:
        if not (box / "files" / f.name / f.version / f.filename).exists():
            missing["files"].append(f"{f.name} {f.version}")
    return {
        "kinds": kinds,
        "missing": missing,
        "missing_total": sum(len(v) for v in missing.values()),
        "bytes": sum(k["bytes"] for k in kinds.values()),
        "files": sum(k["files"] for k in kinds.values()),
    }


def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.exists() else None


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def sync(platform: Platform, box: dict, infra_names: list[str]) -> dict:
    """Four stages and three arrows. Each arrow is 'ok', 'stale' or 'none' with a reason in words."""
    manifest = platform.box_dir / "manifest.json"
    fetched_at = _mtime(manifest)
    needs_at = max([_mtime(p) or 0 for p in platform.needs_dir.glob("*.yml")] + [0]) or None
    uploaded_at = _mtime(platform.state_dir / "upload.json")
    gitea_at = _mtime(platform.state_dir / "gitea.json")
    verified = {n: _mtime(platform.state_dir / f"verify-{n}.json") for n in infra_names}

    arrows = {}
    if not fetched_at:
        arrows["fetch"] = ("none", "nothing fetched yet")
    elif box["missing_total"]:
        arrows["fetch"] = (
            "stale",
            f"{box['missing_total']} item{'s' if box['missing_total'] > 1 else ''} in the needs are not in the box",
        )
    else:
        arrows["fetch"] = ("ok", "every need is in the box")
    if not uploaded_at:
        arrows["upload"] = ("none", "nothing uploaded yet")
    elif fetched_at and fetched_at > uploaded_at + 1:
        arrows["upload"] = ("stale", "the box changed after the last upload")
    else:
        arrows["upload"] = ("ok", "Gitea has the box")
    pending = [n for n, t in verified.items() if not t]
    old = [n for n, t in verified.items() if t and uploaded_at and uploaded_at > t + 1]
    if not infra_names:
        arrows["setup"] = ("none", "no infrastructures in servers.yml")
    elif pending:
        arrows["setup"] = ("none" if len(pending) == len(infra_names) else "stale", f"not set up: {', '.join(pending)}")
    elif old:
        arrows["setup"] = ("stale", f"uploaded after the last verification of {', '.join(old)}; verify again")
    else:
        arrows["setup"] = ("ok", "all nodes verified")
    return {
        "arrows": {k: {"state": s, "why": w} for k, (s, w) in arrows.items()},
        "times": {
            "needs": _iso(needs_at),
            "fetch": _iso(fetched_at),
            "gitea": _iso(gitea_at),
            "upload": _iso(uploaded_at),
            "verify": {n: _iso(t) for n, t in verified.items()},
        },
    }


def preflight(platform: Platform) -> list[dict]:
    """Things this computer needs, each {"name", "ok", "detail", "need"}; `need` says which step cares."""
    checks = []
    for tool, need, why in (
        ("ssh", "all server steps", "talks to the servers"),
        ("openssl", "gitea setup", "makes the CA and certificates"),
        ("docker", "fetch (apt, apk, rpm) and the lab", "resolves OS packages in a container"),
    ):
        path = shutil.which(tool)
        checks.append({"name": tool, "ok": bool(path), "detail": path or f"not found; {why}", "need": need})
    try:
        usage = shutil.disk_usage(platform.root)
        free_gb = usage.free / 1e9
        checks.append(
            {
                "name": "free disk here",
                "ok": free_gb > 5,
                "detail": f"{free_gb:.0f} GB free of {usage.total / 1e9:.0f} GB",
                "need": "fetch and the lab",
            }
        )
    except OSError:
        pass
    try:
        from ..fetch.pypi import pip_command

        out = subprocess.run(pip_command() + ["--version"], capture_output=True, text=True).stdout.strip()
        checks.append({"name": "pip", "ok": True, "detail": out.split(" from ")[0][:40], "need": "fetch (pypi)"})
    except RuntimeError as e:
        checks.append({"name": "pip", "ok": False, "detail": str(e), "need": "fetch (pypi)"})
    return checks


def runs(platform: Platform, limit: int = 50, live: set[str] | None = None) -> list[dict]:
    """Past runs from state/runs/*.log: when, which command, how it ended.

    `live` names the runs this server is still executing; a log without an exit line that is not
    live was cut short (the dashboard was closed) and is reported as exit None with running False."""
    folder = platform.state_dir / "runs"
    if not folder.exists():
        return []
    out = []
    for log in sorted(folder.glob("*.log"), reverse=True)[:limit]:
        text = log.read_text(errors="replace")
        first = text.split("\n", 1)[0]
        tail = text.rstrip().rsplit("\n", 1)[-1]
        m = re.search(r"\[outpost exit (\d+)", tail)
        when = log.stem[:15]
        try:
            stamp = datetime.strptime(when, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
        except ValueError:
            stamp = when
        command = first[2:] if first.startswith("$ ") else first
        args = command.split()[1:] if command.startswith("outpost ") else command.split()
        exit_code = int(m.group(1)) if m else None
        running = m is None and log.stem in (live or set())
        out.append(
            {
                "id": log.stem,
                "when": stamp,
                "command": command,
                "args": args,
                "exit": exit_code,
                "running": running,
                "bytes": log.stat().st_size,
                "summary": "running" if running else explain.summarize(args, text, exit_code),
                "seconds": int(re.search(r"after (\d+)s", tail).group(1))
                if m and re.search(r"after (\d+)s", tail)
                else None,
            }
        )
    return out


def steps(log: str) -> list[dict]:
    """Every '==> ' line of a run as a step; all but the last are done, the last too once the run ended."""
    found = [line[4:].strip() for line in log.splitlines() if line.startswith("==> ")]
    ended = "[outpost exit " in log
    out = []
    for i, text in enumerate(found):
        m = re.match(r"\[(\d+)/(\d+)\]\s*(.*)", text)
        out.append(
            {
                "text": m.group(3) if m else text,
                "n": int(m.group(1)) if m else None,
                "of": int(m.group(2)) if m else None,
                "done": i < len(found) - 1 or ended,
            }
        )
    return out


def box_changes(platform: Platform, needs: Needs) -> dict:
    """Since the last upload: how many files are new in the box (by mtime). And what the box holds that no
    needs file asks for any more, so a stale box is visible before it is uploaded."""
    box = platform.box_dir
    uploaded_at = _mtime(platform.state_dir / "upload.json")
    new_files, new_bytes = 0, 0
    if box.exists():
        for kind in KINDS:
            root = box / kind
            if root.exists():
                for p in root.rglob("*"):
                    if p.is_file() and (not uploaded_at or p.stat().st_mtime > uploaded_at + 1):
                        new_files += 1
                        new_bytes += p.stat().st_size
    unneeded: list[str] = []
    try:
        wanted_images = {oci.parse_ref(r).safe_name for r in needs.images}
    except ValueError:
        wanted_images = set()
    if (box / "images").exists():
        unneeded += [
            f"image {p.name}"
            for p in (box / "images").iterdir()
            if p.is_dir() and p.name not in wanted_images and p.name != "config"
        ]
    wanted_npm = {tarball_name(n.name, n.version) for n in needs.npm}
    if (box / "npm").exists():
        unneeded += [f"npm {p.name}" for p in (box / "npm").glob("*.tgz") if p.name not in wanted_npm]
    wanted_files = {(f.name, f.version) for f in needs.files}
    if (box / "files").exists():
        for name_dir in (box / "files").iterdir():
            for ver in name_dir.iterdir() if name_dir.is_dir() else []:
                if (name_dir.name, ver.name) not in wanted_files:
                    unneeded.append(f"file {name_dir.name} {ver.name}")
    wanted_pypi = {_spec_parts(s)[0] for specs in needs.pypi.values() for s in specs}
    if (box / "pypi").exists():
        for p in (box / "pypi").iterdir():
            if p.suffix in (".whl", ".gz", ".zip"):
                name = _norm(re.split(r"-\d", p.name, 1)[0])
                if (
                    wanted_pypi
                    and name not in wanted_pypi
                    and not any(w.startswith(name) or name.startswith(w) for w in wanted_pypi)
                ):
                    pass  # dependencies pulled along are not in the needs; do not call them unneeded
    return {
        "new_files": new_files,
        "new_bytes": new_bytes,
        "unneeded": unneeded[:50],
        "unneeded_total": len(unneeded),
        "uploaded_at": _iso(uploaded_at),
    }


def search(platform: Platform, needs: Needs, q: str, limit: int = 40) -> dict:
    """Case-insensitive substring search over the needs and the box file names."""
    q = q.strip().lower()
    if len(q) < 2:
        return {"needs": [], "box": []}
    hits = []
    for ref in needs.images:
        if q in ref.lower():
            hits.append({"kind": "image", "text": ref})
    for py, specs in needs.pypi.items():
        hits += [{"kind": f"pypi (py{py})", "text": s} for s in specs if q in s.lower()]
    hits += [{"kind": "npm", "text": f"{n.name}@{n.version}"} for n in needs.npm if q in n.name.lower()]
    for kind, wanted in (("apt", needs.apt), ("apk", needs.apk), ("rpm", needs.rpm)):
        for distro, pkgs in wanted.items():
            hits += [{"kind": f"{kind} ({distro})", "text": p} for p in pkgs if q in p.lower()]
    hits += [{"kind": "file", "text": f"{f.name} {f.version}"} for f in needs.files if q in f.name.lower()]
    box_hits = []
    manifest = platform.box_dir / "manifest.json"
    if manifest.exists():
        for item in json.loads(manifest.read_text()).get("items", []):
            path = item["path"]
            if path.startswith("images/") and "/blobs/" in path:
                continue
            if q in path.lower():
                box_hits.append({"path": path, "size": item["size"]})
    return {"needs": hits[:limit], "box": box_hits[:limit], "more": max(0, len(box_hits) - limit)}
