"""Docker Compose nodes: Docker plus the Compose plugin. Nodes do not know about each other."""

from .. import versions
from . import common


def setup(ctx: common.NodeContext, hosts: list, log=print) -> dict:
    facts = {}
    for host in hosts:
        facts[host.alias] = common.prepare_node(host, ctx, log)
        common.install_docker(host, ctx, log)
    return {"nodes": facts}


def extra_checks(ctx: common.NodeContext) -> list[tuple[str, str]]:
    return [
        ("docker running", "docker info --format '{{.ServerVersion}}'"),
        (f"docker at pinned version {versions.DOCKER}", f"docker info --format '{{{{.ServerVersion}}}}' | grep -q {versions.DOCKER}"),
        (f"compose at pinned version {versions.COMPOSE}", f"docker compose version | grep -q {versions.COMPOSE}"),
        ("compose plugin", "docker compose version"),
        ("buildx plugin", "docker buildx version"),
        ("docker trusts and reaches gitea registry", common.registry_check(ctx)),
        ("container egress hooked", "iptables -C DOCKER-USER -j OUTPOST"),
    ]
