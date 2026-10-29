"""How the nodes reach Gitea and the egress proxy: names, addresses and URLs.

    gitea = gitea_endpoint(system)               # GiteaEndpoint: name, ip, org and every registry URL
    proxy = proxy_endpoint(system, gitea)        # ProxyEndpoint, or None without egress_proxy

servers.yml says which server runs what (GiteaConfig, ProxyConfig). An endpoint says how a node
reaches it, which takes a few rules and sometimes an ssh call, so it is worked out here and not kept
in the AirGappedSystem.

    GiteaEndpoint, ProxyEndpoint          the endpoints
    make_gitea_endpoint, make_proxy_endpoint   the rules, from plain values (easy to test)
    gitea_endpoint, proxy_endpoint, proxy_endpoint_or_none   the same, from a system
"""

from dataclasses import dataclass, field
from urllib.parse import quote

from outpost.client_config import PRIVATE_NETS
from outpost.config import GiteaConfig, ProxyConfig
from outpost.hosts import cached_facts
from outpost.ssh import SshError
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_GITEA, STATE_PROXY


@dataclass(frozen=True)
class GiteaEndpoint:
    """How nodes reach Gitea: its name, its address, and the URL of each registry."""

    name: str  # DNS name, e.g. gitea.internal
    ip: str
    org: str  # organisation holding pip, npm, apt, apk, rpm and files

    @property
    def url(self) -> str:
        return f"https://{self.name}"

    @property
    def pypi_index(self) -> str:
        return f"{self.url}/api/packages/{self.org}/pypi/simple"

    @property
    def npm_registry(self) -> str:
        return f"{self.url}/api/packages/{self.org}/npm/"

    @property
    def apt_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/debian"

    @property
    def apk_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/alpine"

    @property
    def rpm_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/rpm"

    def generic(self, name: str, version: str, filename: str) -> str:
        """The URL of one file in the generic registry."""
        return f"{self.url}/api/packages/{self.org}/generic/{quote(name, safe='')}/{quote(version, safe='')}/{quote(filename, safe='')}"

    @property
    def config_image(self) -> str:
        return f"{self.name}/{self.org}/config:latest"


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where the egress proxy is, as the nodes see it."""

    url: str  # http://<gitea address>:3128, or http://proxy.internal:3128 on another server
    no_proxy: str  # what must never go through the proxy: Gitea, loopback, private networks
    ip: str  # the address the proxy listens on
    name: str  # the name nodes use for it (the Gitea name when it runs on the Gitea server)
    allowed: tuple[str, ...] = field(default_factory=tuple)  # servers.yml egress_proxy.allow
    host: str = ""  # ssh alias of the proxy host
    on_gitea: bool = True  # runs on the Gitea server, so nodes need no new address or name

    def env(self) -> dict[str, str]:
        """The variables, in the order they are written everywhere."""
        return {
            "HTTPS_PROXY": self.url,
            "HTTP_PROXY": self.url,
            "NO_PROXY": self.no_proxy,
            "https_proxy": self.url,
            "http_proxy": self.url,
            "no_proxy": self.no_proxy,
        }


# -- from a system ---------------------------------------------------------------------


def gitea_endpoint(system: AirGappedSystem) -> GiteaEndpoint:
    """How nodes reach Gitea. Raises RuntimeError when its address is not known yet."""
    return make_gitea_endpoint(system.servers.gitea, system.state(STATE_GITEA))


def proxy_endpoint(system: AirGappedSystem, gitea: GiteaEndpoint | None = None) -> ProxyEndpoint | None:
    """How nodes reach the egress proxy, or None when servers.yml has no egress_proxy.

    On the Gitea server its address is Gitea's. Elsewhere it is the address `proxy setup` recorded,
    else the one `outpost hosts` cached, else the proxy host is asked over ssh.

    Raises:
        RuntimeError: Gitea's or the proxy's address is unknown.
        SshError: the proxy host had to be asked and could not be reached.
    """
    proxy = system.servers.egress_proxy
    if not proxy:
        return None
    gitea = gitea or gitea_endpoint(system)
    ip = None if proxy.host == system.servers.gitea.host else _proxy_host_address(system, proxy.host)
    return make_proxy_endpoint(proxy, gitea, system.servers.gitea.host, ip)


def proxy_endpoint_or_none(system: AirGappedSystem, gitea: GiteaEndpoint, log=print) -> ProxyEndpoint | None:
    """The proxy endpoint for the config image; when it cannot be known, a note instead of a failure."""
    try:
        return proxy_endpoint(system, gitea)
    except (RuntimeError, SshError) as e:
        log(f"  note: the egress proxy's address is unknown ({e}); the config image is built without proxy.env")
        return None


def _proxy_host_address(system: AirGappedSystem, alias: str) -> str:
    recorded = system.state(STATE_PROXY) or {}
    if recorded.get("host") == alias and recorded.get("ip"):
        return recorded["ip"]
    cached = cached_facts(system.workspace).get(alias) or {}
    return cached.get("ip") or system.host(alias).ip()


# -- the rules, from plain values ---------------------------------------------------------


def make_gitea_endpoint(gitea: GiteaConfig, gitea_state: dict | None) -> GiteaEndpoint:
    """How nodes reach Gitea: servers.yml's gitea.ip, else the address `gitea setup` recorded.

    Raises:
        RuntimeError: neither is known.
    """
    ip = gitea.ip or (gitea_state or {}).get("ip")
    if not ip:
        raise RuntimeError("Gitea's IP is unknown. Run `outpost gitea setup` first or set gitea.ip in servers.yml.")
    return GiteaEndpoint(name=gitea.name, ip=ip, org=gitea.org)


def make_proxy_endpoint(
    proxy: ProxyConfig, gitea: GiteaEndpoint, gitea_host: str, ip: str | None = None
) -> ProxyEndpoint:
    """How nodes reach the proxy. On the Gitea server it is http://<gitea address>:<port>, an address
    every node already allows; elsewhere http://<egress_proxy.name>:<port>.

    Not the Gitea *name* on the Gitea server: apt's https method (methods/http.cc) clears a proxy that
    no_proxy matches with `Proxy = ""`, which leaves the proxy URI's host in place, and then sends a
    CONNECT to the destination itself when that host equals the destination's. With the proxy at
    http://gitea.internal:3128, every apt fetch from https://gitea.internal went that way and Gitea's
    TLS port hung up ("Reading from proxy failed"). Gitea is reached by name, so an address cannot collide.

    Args:
        proxy: servers.yml's egress_proxy.
        gitea: how nodes reach Gitea.
        gitea_host: the Gitea server's alias.
        ip: the proxy host's address; needed only when it is not the Gitea server.

    Raises:
        RuntimeError: the proxy is on another server and ip is None.
    """
    on_gitea = proxy.host == gitea_host
    name = gitea.name if on_gitea else proxy.name
    address = gitea.ip if on_gitea else ip
    if not address:
        raise RuntimeError(f"the egress proxy host {proxy.host} has no known address; run `outpost proxy setup`")
    no_proxy = [gitea.name, gitea.ip, "localhost", "127.0.0.1", *PRIVATE_NETS] + ([] if on_gitea else [name, address])
    return ProxyEndpoint(
        url=f"http://{address if on_gitea else name}:{proxy.port}",
        no_proxy=",".join(dict.fromkeys(no_proxy)),
        ip=address,
        name=name,
        allowed=tuple(proxy.allow),
        host=proxy.host,
        on_gitea=on_gitea,
    )
