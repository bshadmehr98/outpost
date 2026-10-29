"""Docker Compose nodes: Docker plus the Compose plugin. Nodes do not know about each other."""

from outpost import versions
from outpost.infra import node
from outpost.infra.context import NodeContext
from outpost.infra.verify import output_contains
from outpost.ssh import Host


def setup(ctx: NodeContext, hosts: list[Host], log=print) -> dict:
    """Prepare each node and install Docker on it. Returns {"nodes": {alias: facts}}."""
    facts = {}
    for host in hosts:
        facts[host.alias] = node.prepare_node(host, ctx, log)
        node.install_docker(host, ctx, log)
    return {"nodes": facts}


def verifications(ctx: NodeContext) -> list[tuple[str, str]]:
    """What a compose node runs on top of the node verifications."""
    return [
        ("docker running", "docker info --format '{{.ServerVersion}}'"),
        (
            f"docker at pinned version {versions.DOCKER}",
            output_contains("docker info --format '{{.ServerVersion}}'", versions.DOCKER),
        ),
        (f"compose at pinned version {versions.COMPOSE}", output_contains("docker compose version", versions.COMPOSE)),
        ("compose plugin", "docker compose version"),
        ("buildx plugin", "docker buildx version"),
        ("docker trusts and reaches gitea registry", node.registry_verification(ctx)),
        ("container egress hooked", "iptables -C DOCKER-USER -j OUTPOST"),
    ]
