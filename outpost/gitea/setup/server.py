"""Steps 1 and 2 of `gitea setup`: look at the server, and make the certificates it will serve."""

from pathlib import Path

from outpost import pki, versions
from outpost.config import GiteaConfig
from outpost.system import AirGappedSystem


def connect_and_check_arch(host, gitea_config: GiteaConfig, log=print) -> str:
    """Ask the server what it is. Returns its IP: gitea.ip from servers.yml, else the one it reports.

    Raises:
        RuntimeError: the IP is unknown, or the server's CPU type is not the one the box is built for.
    """
    facts = host.facts()
    ip = gitea_config.ip or facts.get("ip")
    if not ip:
        raise RuntimeError("Could not detect the Gitea host's IP. Set gitea.ip in servers.yml.")
    versions.check_server_arch(host.alias, facts)
    log(
        f"[{host.alias}] {facts.get('id', '?')} {facts.get('codename', '')}, Gitea will be https://{gitea_config.name} ({ip})"
    )
    return ip


def ensure_certificates(system: AirGappedSystem, ip: str) -> tuple[Path, Path]:
    """The CA, and Gitea's certificate for its name and ip. Returns the certificate and its key."""
    pki.ensure_ca(system.workspace)
    return pki.ensure_gitea_cert(system.workspace, system.servers.gitea.name, ip)
