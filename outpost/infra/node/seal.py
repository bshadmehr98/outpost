"""Step 5 of a node: seal it. Everything after this step runs with the internet blocked."""

from outpost import egress
from outpost.infra.context import NodeContext
from outpost.ssh import Host


def seal_node(host: Host, ctx: NodeContext, reason: str) -> str:
    """Install the seal and turn it on. Gitea, the private networks and ctx.allow stay reachable.

    Returns:
        What the seal script printed: "sealed".
    """
    egress.install_seal(host, ctx.gitea.ip, ctx.allow, proxy_user=ctx.proxy_user_for(host.alias))
    return egress.seal_host(host, reason)
