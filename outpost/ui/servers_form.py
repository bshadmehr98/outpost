"""write_servers(): servers.yml from the form on the dashboard's first step."""

import re

import yaml

from outpost.config import ConfigError, load_servers
from outpost.workspace import Workspace

HEADER = (
    "# Which servers play which role. Hosts are aliases from your ~/.ssh/config.\n"
    "# The SSH user must be root or have passwordless sudo. Written by the Outpost dashboard.\n"
)
DEFAULT_PROXY_PORT = "3128"


def write_servers(workspace: Workspace, form: dict) -> None:
    """Write servers.yml from the form. It is validated by reading it back; a bad form keeps the old file.

    Raises:
        ConfigError: the form does not make a valid servers.yml.
    """
    data: dict = {}
    if form.get("ssh_config"):
        data["ssh_config"] = str(form["ssh_config"])
    data["gitea"] = _gitea_section(form.get("gitea") or {})
    data["infrastructures"] = _infrastructures_section(form.get("infrastructures") or {})
    proxy = proxy_form(form.get("egress_proxy")) if "egress_proxy" in form else _current_egress_proxy(workspace)
    if proxy:
        data["egress_proxy"] = proxy
    _write_if_valid(workspace, HEADER + yaml.safe_dump(data, sort_keys=False, default_flow_style=None))


def proxy_form(card) -> dict | None:
    """The egress_proxy section from its card. allow is a textarea (names separated by lines, commas or
    spaces); an empty allow list means no proxy. Host, port and distro only when they differ from the defaults."""
    if not isinstance(card, dict):
        return None
    allow = card.get("allow") or []
    if isinstance(allow, str):
        allow = [a for a in re.split(r"[,\s]+", allow) if a]
    if not allow:
        return None
    section: dict = {}
    if str(card.get("host") or "").strip():
        section["host"] = str(card["host"]).strip()
    port = str(card.get("port") or "").strip()
    if port and port != DEFAULT_PROXY_PORT:
        section["port"] = int(port) if port.isdigit() else port  # load_servers explains a bad one
    section["allow"] = [str(a).strip() for a in allow]
    if str(card.get("distro") or "").strip():
        section["distro"] = str(card["distro"]).strip()
    return section


def _gitea_section(card: dict) -> dict:
    gitea = {"host": str(card.get("host", "")).strip(), "name": str(card.get("name", "")).strip().lower()}
    if card.get("ip"):
        gitea["ip"] = str(card["ip"]).strip()
    return gitea


def _infrastructures_section(cards: dict) -> dict:
    """One entry per card with a usable name (lowercase, dashes); nodes may be a list or "a, b c"."""
    infrastructures = {}
    for name, spec in cards.items():
        key = re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")
        if not key:
            continue
        nodes = spec.get("nodes") or []
        if isinstance(nodes, str):
            nodes = [n for n in re.split(r"[,\s]+", nodes) if n]
        entry = {"type": str(spec.get("type", "compose")), "nodes": nodes}
        if spec.get("args"):
            entry["args"] = str(spec["args"])
        if spec.get("allow"):
            entry["allow"] = spec["allow"]
        infrastructures[key] = entry
    return infrastructures


def _current_egress_proxy(workspace: Workspace) -> dict | None:
    """What servers.yml has now: a form without the egress proxy card keeps it."""
    try:
        current = yaml.safe_load(workspace.servers_file.read_text()) if workspace.servers_file.exists() else None
    except yaml.YAMLError:
        current = None
    return current.get("egress_proxy") if isinstance(current, dict) else None


def _write_if_valid(workspace: Workspace, text: str) -> None:
    """Write a temporary file, load it as servers.yml, and only then put it in place."""
    tmp = workspace.servers_file.with_suffix(".yml.tmp")
    tmp.write_text(text)
    try:
        load_servers(tmp)
    except ConfigError:
        tmp.unlink()
        raise
    tmp.replace(workspace.servers_file)
