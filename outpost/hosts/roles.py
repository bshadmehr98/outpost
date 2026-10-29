"""server_roles(): what each server in servers.yml is for, in words."""

from outpost.config import Servers

# The first node of a multi-node infrastructure, and the others, by infrastructure type.
FIRST_NODE = {"swarm": " manager", "k3s": " server"}
OTHER_NODES = {"swarm": " worker", "k3s": " agent"}


def server_roles(servers: Servers) -> dict[str, list[str]]:
    """{alias: roles}, such as {"gitea-1": ["gitea"], "dev-1": ["dev: compose", "prod: swarm manager"]}.

    The Gitea server comes first, then the nodes in servers.yml's order, then the egress proxy's role.
    """
    roles: dict[str, list[str]] = {servers.gitea.host: ["gitea"]}
    for name, infra in servers.infrastructures.items():
        for index, node in enumerate(infra.nodes):
            roles.setdefault(node, []).append(f"{name}: {infra.type}{_node_part(infra.type, index, len(infra.nodes))}")
    if servers.egress_proxy:
        roles.setdefault(servers.egress_proxy.host, []).append("egress proxy")
    return roles


def _node_part(infra_type: str, index: int, node_count: int) -> str:
    """ " manager", " worker", " server", " agent", or "" for a single node or a compose infrastructure."""
    if node_count == 1:
        return ""
    return (FIRST_NODE if index == 0 else OTHER_NODES).get(infra_type, "")
