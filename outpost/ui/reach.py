"""Reaching servers from the dashboard: can we log in and become root, what is a server's seal, and
fresh facts for the Machines page. Each call says what is wrong in words instead of raising.

The dashboard makes its own Host with a short connect timeout, so a page does not hang on a dead server.
"""

import re
import subprocess
from pathlib import Path

from outpost import egress
from outpost.config import ConfigError, load_servers
from outpost.hosts import refresh_facts
from outpost.ssh import Host, SshError, choose_config_file
from outpost.ssh.options import ssh_options
from outpost.system import AirGappedSystem
from outpost.workspace import Workspace

CONNECT_TIMEOUT = 8  # seconds
SSH_ALIAS = re.compile(r"[A-Za-z0-9._-]+")


def reach_server(workspace: Workspace, alias: str) -> dict:
    """Reach a server the way Outpost will: ssh, then become root.

    Returns:
        {"ok", "message"}, and "user" and "facts" when ssh worked.
    """
    config_file, problem = _ssh_config_for_alias(workspace, alias)
    if problem:
        return {"ok": False, "message": problem}
    plain = subprocess.run(
        ["ssh", *ssh_options(CONNECT_TIMEOUT, config_file), alias, "id -un"], capture_output=True, text=True
    )
    if plain.returncode != 0:
        return {"ok": False, "message": f"ssh {alias} failed: {plain.stderr.strip() or 'no answer'}"}
    user = plain.stdout.strip()
    try:
        facts = _dashboard_host(alias, config_file).facts()
    except SshError as e:
        return {
            "ok": False,
            "user": user,
            "message": f"connected as {user}, but could not become root: {str(e).splitlines()[-1]}",
        }
    return {
        "ok": True,
        "user": user,
        "facts": facts,
        "message": f"connected as {user}; {facts.get('id', '?')} {facts.get('codename', '')} {facts.get('arch', '')} at {facts.get('ip', '?')}",
    }


def server_status(workspace: Workspace, alias: str) -> dict:
    """One server's seal and the last lines of its egress log, its uptime and free disk, read over ssh.

    Returns:
        {"ok": True, "egress", "log", "uptime", "disk"}, or {"ok": False, "message"}.
    """
    config_file, problem = _ssh_config_for_alias(workspace, alias)
    if problem:
        return {"ok": False, "message": problem}
    host = _dashboard_host(alias, config_file)
    try:
        seal = egress.seal_status(host)
        log = host.run("tail -n 40 /var/log/outpost-egress.log 2>/dev/null || true").stdout
        uptime_and_disk = (
            host.run("uptime -p 2>/dev/null; df -h / | awk 'NR==2{print $4\" free of \"$2}'")
            .stdout.strip()
            .splitlines()
        )
    except SshError as e:
        return {"ok": False, "message": str(e).splitlines()[-1]}
    return {
        "ok": True,
        "egress": seal,
        "log": log,
        "uptime": uptime_and_disk[0] if uptime_and_disk else "",
        "disk": uptime_and_disk[1] if len(uptime_and_disk) > 1 else "",
    }


def refresh_hosts(workspace: Workspace, aliases: list[str] | None) -> dict:
    """Ask the servers again (one ssh round trip each) and cache the answers. Odd aliases are dropped;
    none left means every server in servers.yml.

    Raises:
        ConfigError: servers.yml does not load.
    """
    system = AirGappedSystem.load(workspace.root)
    wanted = [alias for alias in (aliases or []) if SSH_ALIAS.fullmatch(alias)] or None
    return refresh_facts(
        system, wanted, log=lambda *_: None, make_host=lambda alias: _dashboard_host(alias, system.ssh_config)
    )


def choose_ssh_config(workspace: Workspace) -> tuple[Path | None, str | None]:
    """Pick the ssh config file the way the CLI does.

    A servers.yml that does not load yet still lets OUTPOST_SSH_CONFIG count.

    Returns:
        (the file, None), with None for ~/.ssh/config; or (None, what is wrong).
    """
    try:
        path = load_servers(workspace.servers_file).ssh_config
    except ConfigError:
        path = None
    try:
        return choose_config_file(path), None
    except SshError as e:
        return None, str(e)


def _ssh_config_for_alias(workspace: Workspace, alias: str) -> tuple[Path | None, str | None]:
    """choose_ssh_config(), after checking that alias looks like an ssh alias."""
    if not SSH_ALIAS.fullmatch(alias or ""):
        return None, "that is not an SSH alias"
    return choose_ssh_config(workspace)


def _dashboard_host(alias: str, config_file: Path | None) -> Host:
    return Host(alias, connect_timeout=CONNECT_TIMEOUT, config_file=config_file)
