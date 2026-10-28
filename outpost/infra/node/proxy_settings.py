"""The egress proxy's settings on a node, and bringing a node up to date when the proxy changes."""

from outpost import client_config
from outpost.client_config import NO_PROXY_ENV, PATH_PROFILE, PATH_PROXY_ENV, RUNNER_HOME
from outpost.infra.context import NodeContext
from outpost.infra.node.names import write_hosts_entries
from outpost.infra.node.seal import seal_node
from outpost.ssh import Host

DOCKER_CLIENT_HOMES = ("/root", RUNNER_HOME)  # whose ~/.docker/config.json gets the proxy settings


def merge_proxy_settings(host: Host, ctx: NodeContext, log=print) -> None:
    """The proxy settings that are merged into existing files rather than written whole: the Docker
    client config of root and of the Actions runner, and /etc/environment.

    Without a proxy, only what Outpost wrote before is removed, and /etc/outpost/proxy.env is left as a
    comment, so `env_file:` can always name it.
    """
    if not ctx.proxy:
        host.write(PATH_PROXY_ENV, NO_PROXY_ENV)
    for home in DOCKER_CLIENT_HOMES:
        path = f"{home}/.docker/config.json"
        existing = host.run(f"cat {path} 2>/dev/null || true").stdout
        updated = client_config.docker_client_config(existing, ctx.proxy, ctx.gitea.name)
        if updated is not None:
            host.write(path, updated, mode="0600")
    host.run(client_config.environment_script(ctx.proxy), raise_on_failure=True)
    if ctx.proxy:
        log(
            f"  [{host.alias}] egress proxy {ctx.proxy.url} for programs that opt in (HTTPS_PROXY), containers through the Docker client config"
        )


def apply_proxy_settings(host: Host, ctx: NodeContext, log=print) -> None:
    """Bring a node that is already set up up to date with the egress proxy (or its removal), without
    redoing the rest of prepare_node: names, files, merged settings, and the seal when the proxy's
    address is new to the node."""
    write_hosts_entries(host, ctx)
    files = {PATH_PROFILE: client_config.profile_script(ctx.gitea, ctx.proxy)}
    if ctx.proxy:
        files[PATH_PROXY_ENV] = client_config.proxy_env(ctx.proxy)
    for path, content in files.items():
        host.write(path, content)
    merge_proxy_settings(host, ctx, log)
    if ctx.proxy and not ctx.proxy.on_gitea:
        log(f"  [{host.alias}] egress: {seal_node(host, ctx, 'proxy setup')} (allows the proxy at {ctx.proxy.ip})")
