"""The steps every node goes through, whatever runs on it afterwards.

    facts = prepare_node(host, ctx, log=print)     # steps 1 to 6 below
    install_docker(host, ctx, log=print)           # compose and swarm nodes only

  1. know the Gitea server by name              /etc/hosts, kept at every boot          names.py
  2. point pip, npm, apt and Docker at Gitea    the client files (client_config)        trust.py
     and trust the private CA                   system trust store + explicit paths
  3. prove Gitea answers over trusted TLS                                               trust.py
  4. OS packages from Gitea, by family          apt (Debian/Ubuntu), apk (Alpine) or    os_packages.py
                                                dnf (RHEL family): fetch the key, park
                                                the stock sources, write the Gitea one
  5. seal egress                                outpost-egress seal                     seal.py
  6. install CoreDNS from Gitea                 so containers can resolve the Gitea name dns.py

Everything after step 5 runs with the internet blocked. That is the point: if setup succeeds,
the node provably needs nothing from outside.

With an egress proxy in servers.yml, steps 1 and 2 also carry its settings: the proxy's name in
/etc/hosts when it is not on the Gitea server, /etc/outpost/proxy.env, the profile.d exports,
/etc/environment and the Docker client config (proxy_settings.py). Step 5 then allows the proxy's
address too. apply_proxy_settings brings a node that is already set up up to date.

    names.py           write_hosts_entries
    trust.py           write_client_files, trust_ca, check_reaches_gitea
    proxy_settings.py  merge_proxy_settings, apply_proxy_settings
    os_packages.py     configure_os_packages, configure_apt_sources, configure_apk_sources, configure_rpm_sources
    seal.py            seal_node
    dns.py             install_node_dns, DNS_UNIT
    docker.py          install_docker, DOCKER_UNIT, registry_verification
    download.py        download_from_gitea

Code outside this folder uses only the names below.
"""

from outpost import versions
from outpost.infra.context import NodeContext
from outpost.infra.node.dns import DNS_UNIT, install_node_dns
from outpost.infra.node.docker import DOCKER_UNIT, install_docker, registry_verification
from outpost.infra.node.download import download_from_gitea
from outpost.infra.node.names import write_hosts_entries
from outpost.infra.node.os_packages import (
    configure_apk_sources,
    configure_apt_sources,
    configure_os_packages,
    configure_rpm_sources,
)
from outpost.infra.node.proxy_settings import apply_proxy_settings, merge_proxy_settings
from outpost.infra.node.seal import seal_node
from outpost.infra.node.trust import check_reaches_gitea, trust_ca, write_client_files
from outpost.ssh import Host

__all__ = [
    "DNS_UNIT",
    "DOCKER_UNIT",
    "apply_proxy_settings",
    "check_reaches_gitea",
    "configure_apk_sources",
    "configure_apt_sources",
    "configure_os_packages",
    "configure_rpm_sources",
    "download_from_gitea",
    "install_docker",
    "install_node_dns",
    "merge_proxy_settings",
    "prepare_node",
    "registry_verification",
    "seal_node",
    "trust_ca",
    "write_client_files",
    "write_hosts_entries",
]


def prepare_node(host: Host, ctx: NodeContext, log=print) -> dict:
    """Steps 1 to 6: names, client files, trust, Gitea reachable, OS packages, seal, node DNS.

    Returns:
        The node's facts (host.facts()).

    Raises:
        RuntimeError: the node is of another CPU type, or cannot reach Gitea.
        SshError: the node could not be reached, or a step failed on it.
    """
    log(f"==> {host.alias}: connecting and configuring the clients")
    facts = host.facts()
    log(
        f"  [{host.alias}] {facts.get('id', '?')} {facts.get('codename', '')} {facts.get('arch', '')} ip={facts.get('ip', '?')}"
    )
    versions.check_server_arch(host.alias, facts, ctx.arch)

    write_hosts_entries(host, ctx)
    write_client_files(host, ctx, node_ip=facts.get("ip") or host.ip())
    merge_proxy_settings(host, ctx, log)
    trust_ca(host)
    check_reaches_gitea(host, ctx, log)
    configure_os_packages(host, ctx, facts, log)

    log(f"==> {host.alias}: switching the internet off")
    log(f"  [{host.alias}] egress: {seal_node(host, ctx, f'setup {ctx.infra_name}'.strip())}")

    log(f"==> {host.alias}: installing node DNS from Gitea")
    install_node_dns(host, ctx, log)
    return facts
