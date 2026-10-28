"""The facts cache: one state/hosts/<alias>.json per server, written after each inspection."""

import json
import time
from collections.abc import Callable

from outpost.hosts.inspect import inspect_server
from outpost.hosts.roles import server_roles
from outpost.ssh import Host
from outpost.system import AirGappedSystem
from outpost.workspace import Workspace


def record_facts(workspace: Workspace, alias: str, facts: dict) -> dict:
    """Write state/hosts/<alias>.json, with the alias and the time it was seen. Returns what was written."""
    folder = workspace.hosts_state_dir
    folder.mkdir(parents=True, exist_ok=True)
    facts = {**facts, "alias": alias, "seen": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (folder / f"{alias}.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")
    return facts


def cached_facts(workspace: Workspace) -> dict[str, dict]:
    """{alias: facts} from state/hosts/, for the dashboard and the report. Never talks to a server;
    a file that does not read as JSON is left out."""
    folder = workspace.hosts_state_dir
    if not folder.exists():
        return {}
    facts = {}
    for path in sorted(folder.glob("*.json")):
        try:
            facts[path.stem] = json.loads(path.read_text())
        except ValueError:
            continue
    return facts


def inspect_and_record(system: AirGappedSystem, host: Host) -> dict:
    """Inspect one server and cache what it runs. The setup and verify commands call it at the end."""
    return record_facts(system.workspace, host.alias, inspect_server(host))


def refresh_facts(
    system: AirGappedSystem,
    aliases: list[str] | None = None,
    log=print,
    make_host: Callable[[str], Host] | None = None,
) -> dict[str, dict]:
    """Inspect some servers (default: every server in servers.yml), cache the facts, and log one line each.

    Args:
        make_host: how to get a Host for an alias; default system.host. The dashboard passes its own,
            with a shorter connect timeout.

    Returns:
        {alias: facts} for every server asked.
    """
    make_host = make_host or system.host
    refreshed = {}
    for alias in aliases or list(server_roles(system.servers)):
        facts = record_facts(system.workspace, alias, inspect_server(make_host(alias)))
        refreshed[alias] = facts
        log(_one_line(alias, facts))
    return refreshed


def _one_line(alias: str, facts: dict) -> str:
    if not facts["reachable"]:
        return f"  {alias:14} unreachable: {facts['error']}"
    installed = ", ".join(f"{k} {v}" for k, v in facts["installed"].items() if v) or "nothing installed yet"
    return f"  {alias:14} {facts.get('pretty', '?'):24} {facts.get('ip', '?'):15} {facts['egress']:14} {installed}"
