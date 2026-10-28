"""Docker Swarm: Docker on every node, the first node is the manager, the rest join as workers."""

from .. import versions
from . import common
from .context import NodeContext


def setup(ctx: NodeContext, hosts: list, log=print) -> dict:
    facts = {}
    for host in hosts:
        facts[host.alias] = common.prepare_node(host, ctx, log)
        common.install_docker(host, ctx, log)

    manager = hosts[0]
    manager_ip = facts[manager.alias]["ip"]
    state = manager.run("docker info --format '{{.Swarm.LocalNodeState}}'", raise_on_failure=True).stdout.strip()
    if state != "active":
        manager.run(f"docker swarm init --advertise-addr {manager_ip} {ctx.args}".strip(), raise_on_failure=True)
        log(f"  [{manager.alias}] swarm initialised (manager)")
    else:
        log(f"  [{manager.alias}] already a swarm node")
    token = manager.run("docker swarm join-token -q worker", raise_on_failure=True).stdout.strip()

    for host in hosts[1:]:
        if (
            host.run("docker info --format '{{.Swarm.LocalNodeState}}'", raise_on_failure=True).stdout.strip()
            == "active"
        ):
            log(f"  [{host.alias}] already joined")
            continue
        host.run(f"docker swarm join --token {token} {manager_ip}:2377", raise_on_failure=True)
        log(f"  [{host.alias}] joined as worker")
    return {"nodes": facts, "manager": manager.alias}


def extra_checks(ctx: NodeContext) -> list[tuple[str, str]]:
    return [
        ("docker running", "docker info --format '{{.ServerVersion}}'"),
        (
            f"docker at pinned version {versions.DOCKER}",
            f"docker info --format '{{{{.ServerVersion}}}}' | grep -q {versions.DOCKER}",
        ),
        (f"compose at pinned version {versions.COMPOSE}", f"docker compose version | grep -q {versions.COMPOSE}"),
        ("swarm active", "test \"$(docker info --format '{{.Swarm.LocalNodeState}}')\" = active"),
        ("docker trusts and reaches gitea registry", common.registry_check(ctx)),
        ("container egress hooked", "iptables -C DOCKER-USER -j OUTPOST"),
    ]
