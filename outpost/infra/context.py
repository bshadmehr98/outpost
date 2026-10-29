"""NodeContext: what every setup step on a node needs to know, and how to build it from a system.

ctx = node_context(system, system.infra("dev"))    # for the nodes of one infrastructure
ctx = proxy_host_context(system)                   # for the egress proxy's own server
"""

from dataclasses import dataclass, field

from outpost.config import ConfigError, ProxyConfig
from outpost.egress import PROXY_USER
from outpost.endpoints import GiteaEndpoint, ProxyEndpoint, gitea_endpoint, make_proxy_endpoint, proxy_endpoint
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_UPLOAD


@dataclass
class NodeContext:
    """What a node's setup needs: how to reach Gitea and the proxy, the CA, the CPU type, and what Gitea has."""

    gitea: GiteaEndpoint
    ca_pem: str
    arch: str  # the box's CPU type, amd64 or arm64: every node must be of it
    distros: list[str] = field(default_factory=list)  # apt distros that exist in Gitea
    apk_branches: list[str] = field(default_factory=list)  # Alpine branches that exist in Gitea
    rpm_distros: list[str] = field(default_factory=list)  # rpm distros (Gitea groups) that exist in Gitea
    allow: list[str] = field(default_factory=list)  # extra reachable addresses (other nodes, user list)
    infra_name: str = ""
    args: str = ""  # extra flags for the runtime (k3s server, swarm init)
    proxy: ProxyEndpoint | None = None  # the egress proxy, when servers.yml has one

    def proxy_user_for(self, alias: str) -> str | None:
        """The owner exception goes only into the seal of the server that runs the proxy."""
        return PROXY_USER if self.proxy and self.proxy.host == alias else None

    def hosts_entries(self) -> list[tuple[str, str]]:
        """The (address, name) lines a node needs in /etc/hosts."""
        g = self.gitea
        extra = [(self.proxy.ip, self.proxy.name)] if self.proxy and not self.proxy.on_gitea else []
        return [(g.ip, g.name), *extra]


def node_context(system: AirGappedSystem, infra) -> NodeContext:
    """The context for the nodes of one infrastructure (an InfraConfig).

    The nodes may reach the infrastructure's `allow:` list, each other, and the egress proxy's address
    when it runs on its own server (on the Gitea server it is Gitea's address, which is always allowed).

    Raises:
        RuntimeError: Gitea is not set up yet (its address or the CA is missing).
    """
    gitea = gitea_endpoint(system)
    nodes = system.nodes(infra.name)
    node_ips = [node.ip() for node in nodes] if len(nodes) > 1 else []
    proxy = proxy_endpoint(system, gitea)
    proxy_ip = [proxy.ip] if proxy and not proxy.on_gitea else []
    uploaded = system.state(STATE_UPLOAD) or {}  # what the last upload put into Gitea
    return NodeContext(
        gitea=gitea,
        ca_pem=system.ca_pem(),
        arch=system.arch,
        distros=uploaded.get("distros", []),
        apk_branches=uploaded.get("apk_branches", []),
        rpm_distros=uploaded.get("rpm_distros", []),
        allow=list(dict.fromkeys(infra.allow + node_ips + proxy_ip)),
        infra_name=infra.name,
        args=infra.args,
        proxy=proxy,
    )


def proxy_host_context(system: AirGappedSystem) -> NodeContext:
    """The context for the egress proxy's own server.

    Sealed, it may reach what Gitea may reach when it is the Gitea server; elsewhere what the
    infrastructure it belongs to may reach, if any.

    Raises:
        ConfigError: servers.yml has no egress_proxy section.
    """
    proxy = egress_proxy_config(system)
    gitea = gitea_endpoint(system)
    on_gitea = proxy.host == system.servers.gitea.host
    address = None if on_gitea else system.host(proxy.host).ip()
    uploaded = system.state(STATE_UPLOAD) or {}  # what the last upload put into Gitea
    return NodeContext(
        gitea=gitea,
        ca_pem=system.ca_pem(),
        arch=system.arch,
        distros=uploaded.get("distros", []),
        apk_branches=uploaded.get("apk_branches", []),
        rpm_distros=uploaded.get("rpm_distros", []),
        allow=_proxy_host_allow(system, proxy, on_gitea),
        infra_name="egress proxy",
        proxy=make_proxy_endpoint(proxy, gitea, system.servers.gitea.host, address),
    )


def egress_proxy_config(system: AirGappedSystem) -> ProxyConfig:
    """servers.yml's egress_proxy. Raises ConfigError, with what to add, when there is none."""
    proxy = system.servers.egress_proxy
    if not proxy:
        raise ConfigError(
            "servers.yml has no egress_proxy section. Add one (allow: [api.twilio.com]) to give sealed "
            "nodes named exceptions; see docs/architecture/proxy.md."
        )
    return proxy


def _proxy_host_allow(system: AirGappedSystem, proxy: ProxyConfig, on_gitea: bool) -> list[str]:
    if on_gitea:
        return list(system.servers.gitea.allow)
    member = next((infra for infra in system.servers.infrastructures.values() if proxy.host in infra.nodes), None)
    return list(member.allow) if member else []
