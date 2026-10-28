"""Step 7 of `gitea setup`: seal the Gitea server, keeping the egress proxy's exception when it runs there."""

from outpost import egress
from outpost.egress import PROXY_USER
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_PROXY


def seal_gitea_server(system: AirGappedSystem, host, ip: str, log=print) -> None:
    """Install the seal on the Gitea server and turn it on.

    When `outpost proxy setup` put the egress proxy on this server, its user keeps its one exception.
    """
    gitea_config = system.servers.gitea
    proxy_here = _egress_proxy_runs_here(system)
    egress.install_seal(host, ip, gitea_config.allow, proxy_user=PROXY_USER if proxy_here else None)
    if proxy_here:
        log(f"[{host.alias}] the egress proxy runs here: its user {PROXY_USER} keeps its exception (TCP 443 and 80)")
    log(f"[{host.alias}] egress: {egress.seal_host(host, 'gitea setup')}")


def _egress_proxy_runs_here(system: AirGappedSystem) -> bool:
    """True when servers.yml puts the egress proxy on the Gitea server and `proxy setup` installed it there."""
    proxy, gitea_host = system.servers.egress_proxy, system.servers.gitea.host
    proxy_state = system.state(STATE_PROXY) or {}
    return bool(proxy and proxy.host == gitea_host and proxy_state.get("host") == gitea_host)
