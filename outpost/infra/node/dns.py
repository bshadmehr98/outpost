"""Step 6 of a node: CoreDNS from Gitea, so containers can resolve the Gitea name.

Containers cannot see the node's /etc/hosts. CoreDNS answers from it (client_config.corefile) on the
node's address, and Docker and k3s hand that address to every container as its DNS server.
"""

from outpost import versions
from outpost.client_config import PATH_COREFILE
from outpost.infra.context import NodeContext
from outpost.infra.node.download import download_from_gitea
from outpost.ssh import Host

DNS_UNIT_PATH = "/etc/systemd/system/outpost-dns.service"
COREDNS = "/usr/local/bin/coredns"

DNS_UNIT = f"""[Unit]
Description=Outpost node DNS (CoreDNS: answers the Gitea name for containers)
After=network-online.target
Wants=network-online.target
Before=docker.service k3s.service k3s-agent.service

[Service]
ExecStart={COREDNS} -conf {PATH_COREFILE}
Restart=always
RestartSec=2
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""

START_DNS = (
    "systemctl daemon-reload && systemctl enable outpost-dns >/dev/null 2>&1; "
    "systemctl restart outpost-dns && sleep 1 && systemctl is-active outpost-dns"
)


def install_node_dns(host: Host, ctx: NodeContext, log=print) -> None:
    """Install CoreDNS at the pinned version (unless it is there already), and start its unit."""
    url = ctx.gitea.generic("coredns", versions.COREDNS, f"coredns_{versions.COREDNS}_linux_{ctx.arch}.tgz")
    installed = host.run(f"{COREDNS} -version 2>/dev/null || true", raise_on_failure=True).stdout
    if versions.COREDNS not in installed:
        host.run(
            f"{download_from_gitea(url, '/tmp/coredns.tgz')}\n"
            f"tar -xzf /tmp/coredns.tgz -C /usr/local/bin coredns && chmod 755 {COREDNS} && rm -f /tmp/coredns.tgz\n",
            raise_on_failure=True,
        )
    host.write(DNS_UNIT_PATH, DNS_UNIT)
    host.run(START_DNS, raise_on_failure=True)
    log(f"  [{host.alias}] node dns (coredns {versions.COREDNS}) answers {ctx.gitea.name} for containers")
