"""The recipes behind `outpost proxy setup` and `outpost proxy verify`, for one system."""

import time

from outpost import hosts
from outpost.endpoints import ProxyEndpoint
from outpost.infra import k3s, node
from outpost.infra.context import egress_proxy_config, node_context, proxy_host_context
from outpost.infra.proxy.setup import set_up_on_host
from outpost.infra.proxy.verify import verify_proxy_host
from outpost.infra.verify import log_verifications
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_PROXY


def set_up_egress_proxy(system: AirGappedSystem, log=print) -> ProxyEndpoint:
    """Install and seal the egress proxy on its server, record it in state/proxy.json, then give the
    nodes of every infrastructure that is already set up the proxy settings.

    Returns:
        Where the proxy is, as the nodes see it.

    Raises:
        ConfigError: servers.yml has no egress_proxy section.
    """
    proxy = egress_proxy_config(system)
    ctx = proxy_host_context(system)
    endpoint = ctx.proxy
    assert endpoint is not None  # proxy_host_context always sets it
    log(f"Setting up the egress proxy on {proxy.host}: {endpoint.url}, allowing {', '.join(proxy.allow)}")
    result = set_up_on_host(
        system.host(proxy.host),
        ctx,
        proxy,
        egress_allow=ctx.allow,
        client_allow=_addresses_that_may_use_the_proxy(system),
        log=log,
    )
    system.write_state(STATE_PROXY, {**result, "when": _now()})
    update_set_up_nodes(system, log)
    log()
    return endpoint


def verify_egress_proxy(system: AirGappedSystem, log=print) -> bool:
    """Run the proxy's verifications on its server, record them in state/proxy.json, and record what
    the server runs. True when every verification passed.

    Raises:
        ConfigError: servers.yml has no egress_proxy section.
    """
    proxy = egress_proxy_config(system)
    ctx = proxy_host_context(system)
    host = system.host(proxy.host)
    log(f"==> verifying the egress proxy on {proxy.host}")
    results = verify_proxy_host(host, ctx, proxy)
    all_passed = log_verifications(proxy.host, results, log)
    verification = {"result": "ok" if all_passed else "problems", "checks": results, "when": _now()}
    system.write_state(STATE_PROXY, {**(system.state(STATE_PROXY) or {}), "verify": verification})
    hosts.record(system.workspace, proxy.host, hosts.inspect(host))
    return all_passed


def update_set_up_nodes(system: AirGappedSystem, log=print) -> None:
    """proxy.env, the profile and the Docker client config on every node already set up; k3s gets its ConfigMap."""
    infras = system.set_up_infras()
    if infras:
        log(
            f"==> nodes of {', '.join(infra.name for infra in infras)}: proxy settings (proxy.env, profile, Docker client config)"
        )
    for infra in infras:
        ctx = node_context(system, infra)
        nodes = system.nodes(infra.name)
        for host in nodes:
            node.apply_proxy_settings(host, ctx, log=log)
        if infra.type == "k3s":
            k3s.apply_configmap(nodes[0], ctx.proxy, log=log)


def _addresses_that_may_use_the_proxy(system: AirGappedSystem) -> list[str]:
    """Gitea's `allow:` list and every infrastructure's: the proxy accepts clients from those (and private networks)."""
    servers = system.servers
    return list(dict.fromkeys(servers.gitea.allow + [a for i in servers.infrastructures.values() for a in i.allow]))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
