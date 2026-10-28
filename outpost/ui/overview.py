"""overview(): everything the dashboard's pages show, in one JSON document (GET /api/overview).

It works while servers.yml, mirrors.yml or a needs file is broken: each part that cannot be read
says why in its own key, and the rest of the page still loads.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from outpost import demo, lab, versions
from outpost.checks import load_checks
from outpost.config import ConfigError, Servers, load_mirrors, load_servers
from outpost.hosts import cached_facts, pinned_versions, server_roles, version_drift
from outpost.needs import Needs, all_needs, load_needs, needs_and_pinned_files, read_needs_file
from outpost.report import report_data, verdict
from outpost.system import AirGappedSystem
from outpost.ui.runs import RUNS, live_runs
from outpost.ui.status import box_changes, box_status, preflight, runs, sync
from outpost.workspace import GITEA_TOKEN, STATE_DEMO_STATUS, Workspace

RECENT_RUNS = 8  # runs shown on the overview; GET /api/runs has them all


def overview(workspace: Workspace) -> dict:
    """The whole dashboard state. index.html reads these keys; keep them as they are."""
    servers, servers_error = _servers_or_error(workspace)
    data: dict = {"root": str(workspace.root), "servers": _servers_view(servers), "error": servers_error}
    full_needs = _add_needs(data, workspace, servers)
    data["box_status"] = box_status(workspace, full_needs)
    data["box_changes"] = box_changes(workspace, full_needs)
    data["sync"] = sync(workspace, data["box_status"], list(servers.infrastructures) if servers else [])
    data["preflight"] = preflight(workspace)
    data["runs"] = runs(workspace, limit=RECENT_RUNS, live=live_runs())
    data["needs_files"] = needs_files(workspace)
    data["box"] = _box_manifest_summary(workspace)
    data["state"] = {name: workspace.read_state(name) for name in workspace.state_names()}
    data["files"] = _editable_files(workspace)
    data["checks"] = _checks(workspace)
    data["secrets"] = {GITEA_TOKEN: (workspace.secrets_dir / GITEA_TOKEN).exists()}
    data["ssh_aliases"] = ssh_aliases()
    data["lab"] = lab_status()
    data["versions"] = pinned_versions()
    data["running"] = [{"id": run_id, "args": RUNS[run_id]["args"]} for run_id in live_runs()]
    data["demo"] = _demo(workspace, servers)
    data["hosts"] = cached_facts(workspace)
    data["roles"] = server_roles(servers) if servers else {}
    data["drift"] = {alias: version_drift(facts) for alias, facts in data["hosts"].items() if version_drift(facts)}
    data["backups"] = list_backups(workspace)
    data["report"] = verdict(report_data(workspace, servers))
    return data


def needs_files(workspace: Workspace) -> list[dict]:
    """One entry per needs/*.yml: what it declares, and the error when it does not read cleanly."""
    found = []
    for path in sorted(workspace.needs_dir.glob("*.yml")) + sorted(workspace.needs_dir.glob("*.yaml")):
        entry = {"file": f"needs/{path.name}", "name": path.stem, "summary": None, "error": None, "raw": None}
        try:
            raw = yaml.safe_load(path.read_text()) or {}
            entry["raw"] = raw if isinstance(raw, dict) else None
            entry["name"] = str(raw.get("name", path.stem)) if isinstance(raw, dict) else path.stem
            needs = Needs()
            read_needs_file(path, needs)
            entry["summary"] = needs.summary()
        except (ConfigError, yaml.YAMLError) as e:
            entry["error"] = str(e)
        found.append(entry)
    return found


def needs_detail(needs: Needs) -> dict:
    """The merged list `outpost fetch` works from, as the Needs page shows it."""
    return {
        "images": needs.images,
        "pypi": dict(needs.pypi),
        "npm": [f"{n.name}@{n.version}" for n in needs.npm],
        "apt": needs.apt,
        "apk": needs.apk,
        "rpm": needs.rpm,
        "files": [{"name": f.name, "version": f.version} for f in needs.files],
        "summary": needs.summary(),
    }


def ssh_aliases() -> list[str]:
    """Host aliases from ~/.ssh/config, for the dropdowns. Wildcards are left out."""
    config = Path.home() / ".ssh" / "config"
    if not config.exists():
        return []
    aliases = []
    for m in re.finditer(r"^\s*Host\s+(.+)$", config.read_text(errors="replace"), re.M):
        aliases += [a for a in m.group(1).split() if not any(c in a for c in "*?!")]
    return aliases


def lab_status() -> dict:
    """Is Docker here, and which lab servers are running."""
    if not shutil.which("docker"):
        return {"docker": False, "running": []}
    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=^lab-"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split()
    except (subprocess.SubprocessError, OSError):
        names = []
    return {"docker": True, "running": sorted(name for name in names if name in lab.SERVERS)}


def list_backups(workspace: Workspace) -> list[dict]:
    """The Gitea backups in backups/, newest first, for the restore picker."""
    folder = workspace.backups_dir
    if not folder.exists():
        return []
    return [
        {
            "file": str(path.relative_to(workspace.root)),
            "bytes": path.stat().st_size,
            "when": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime)),
        }
        for path in sorted(folder.glob("*.tar.gz"), reverse=True)
    ]


# -- the parts of the overview ------------------------------------------------------------------


def _servers_or_error(workspace: Workspace) -> tuple[Servers | None, str | None]:
    """servers.yml and no error, or None and what is wrong with it. Downloads use its arch from now on."""
    try:
        servers = load_servers(workspace.servers_file)
    except ConfigError as e:
        return None, str(e)
    versions.use_arch(servers.arch)
    return servers, None


def _servers_view(servers: Servers | None) -> dict | None:
    if not servers:
        return None
    return {
        "gitea": vars(servers.gitea),
        "infrastructures": {name: vars(infra) for name, infra in servers.infrastructures.items()},
        "ssh_config": str(servers.ssh_config) if servers.ssh_config else None,
        "is_lab": servers.gitea.host == "lab-gitea",
        "arch": servers.arch,
        "egress_proxy": vars(servers.egress_proxy) if servers.egress_proxy else None,
    }


def _add_needs(data: dict, workspace: Workspace, servers: Servers | None) -> Needs:
    """Fill data["needs"] (the needs files' summary) and data["needs_detail"] (everything fetch will get).

    Returns:
        Everything fetch will get, for the box status; empty when a needs file or mirrors.yml is broken,
        whose error is then in data["needs"]["error"].
    """
    try:
        data["needs"] = load_needs(workspace.needs_dir).summary()
        full = _everything_to_fetch(workspace, servers)
    except ConfigError as e:
        data["needs"], data["needs_detail"] = {"error": str(e)}, None
        return Needs()
    data["needs_detail"] = needs_detail(full)
    return full


def _everything_to_fetch(workspace: Workspace, servers: Servers | None) -> Needs:
    """needs.all_needs, with tinyproxy left out while its distro is unknown; while servers.yml does not
    load, the needs files and Gitea's own download."""
    if servers:
        return all_needs(AirGappedSystem(workspace, servers), skip_unknown_proxy_distro=True)
    return needs_and_pinned_files(workspace.needs_dir, load_mirrors(workspace.mirrors_file), set())


def _box_manifest_summary(workspace: Workspace) -> dict | None:
    """box/manifest.json without its list of items (the page needs only the totals)."""
    manifest = workspace.box_dir / "manifest.json"
    if not manifest.exists():
        return None
    summary = json.loads(manifest.read_text())
    summary.pop("items", None)
    return summary


def _editable_files(workspace: Workspace) -> list[str]:
    return (
        ["servers.yml", "mirrors.yml"]
        + [f"needs/{p.name}" for p in sorted(workspace.needs_dir.glob("*.yml"))]
        + [f"checks/{p.name}" for p in sorted(workspace.checks_dir.glob("*.yml"))]
    )


def _checks(workspace: Workspace) -> list[dict]:
    try:
        return [
            {"name": c.name, "description": c.description, "infra": c.infra, "requires": c.requires}
            for c in load_checks(workspace.checks_dir)
        ]
    except ConfigError as e:
        return [{"name": "error", "description": str(e), "infra": []}]


def _demo(workspace: Workspace, servers: Servers | None) -> dict:
    local = workspace.demo_dir.exists()
    return {
        "local": local,
        "urls": demo.demo_urls(AirGappedSystem(workspace, servers)) if servers else {},
        "files": sorted(demo.project_files(workspace.demo_dir)) if local else [],
        "status": workspace.read_state(STATE_DEMO_STATUS),
    }
