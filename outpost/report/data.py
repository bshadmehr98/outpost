"""report_data(): everything the report shows, read from state/, state/hosts/ and servers.yml."""

import time

from outpost import __version__, versions
from outpost.config import ConfigError, Servers, load_servers
from outpost.hosts import cached_facts, pinned_versions, server_roles
from outpost.workspace import (
    STATE_BACKUP,
    STATE_DEMO_STATUS,
    STATE_GITEA,
    STATE_PROXY,
    STATE_UPLOAD,
    Workspace,
    check_state,
    infra_state,
    verify_state,
)


def servers_or_none(workspace: Workspace) -> Servers | None:
    """servers.yml, or None when it is missing or does not load: the report shows a half-built system too."""
    try:
        return load_servers(workspace.servers_file)
    except ConfigError:
        return None


def report_data(workspace: Workspace, servers: Servers | None) -> dict:
    """What the report shows, as plain data (the dashboard reads its verdict too).

    Args:
        servers: servers.yml, or None when it does not load; then the page has no infrastructures and roles.
    """
    state = {name: workspace.read_state(name) for name in workspace.state_names()}
    demo = state.get(STATE_DEMO_STATUS) or {}
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "outpost": __version__,
        "root": str(workspace.root),
        "gitea": {**(state.get(STATE_GITEA) or {}), "config": vars(servers.gitea) if servers else {}},
        "upload": state.get(STATE_UPLOAD) or {},
        "backup": state.get(STATE_BACKUP) or {},
        "infras": _infrastructures(workspace, servers, state),
        "hosts": cached_facts(workspace),
        "roles": server_roles(servers) if servers else {},
        "pinned": pinned_versions(),
        "arch": servers.arch if servers else versions.ARCH,
        "serving": demo.get("serving") or {},
        "demo_at": _when_written(workspace, STATE_DEMO_STATUS),
        "proxy": {
            "config": vars(servers.egress_proxy) if servers and servers.egress_proxy else None,
            "state": state.get(STATE_PROXY) or {},
        },
    }


def _infrastructures(workspace: Workspace, servers: Servers | None, state: dict) -> dict:
    """Per infrastructure: its type and nodes, the last verification and smoke checks, and when they ran."""
    if not servers:
        return {}
    return {
        name: {
            "type": infra.type,
            "nodes": infra.nodes,
            "verify": state.get(verify_state(name)) or {},
            "checks": (state.get(check_state(name)) or {}).get("results") or [],
            "verified_at": _when_written(workspace, verify_state(name)),
            "checked_at": _when_written(workspace, check_state(name)),
            "set_up": bool(state.get(infra_state(name))),
        }
        for name, infra in servers.infrastructures.items()
    }


def _when_written(workspace: Workspace, state_name: str) -> str | None:
    """When state/<name>.json was written, as "2026-01-31 14:05", or None."""
    mtime = workspace.state_mtime(state_name)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else None
