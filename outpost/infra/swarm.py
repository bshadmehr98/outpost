"""Docker Swarm: Docker on every node, the first node is the manager, the rest join as workers."""

from outpost import versions
from outpost.infra import node
from outpost.infra.context import NodeContext
from outpost.ssh import Host

SWARM_STATE = "docker info --format '{{.Swarm.LocalNodeState}}'"  # "active" once the node is in a swarm


def setup(ctx: NodeContext, hosts: list[Host], log=print) -> dict:
    """Prepare each node and install Docker on it, start the swarm on the first, and join the rest.

    Returns:
        {"nodes": {alias: facts}, "manager": alias}
    """
    facts = {}
    for host in hosts:
        facts[host.alias] = node.prepare_node(host, ctx, log)
        node.install_docker(host, ctx, log)
    manager, workers = hosts[0], hosts[1:]
    manager_ip = facts[manager.alias]["ip"]
    join_token = _start_swarm(manager, manager_ip, ctx.args, log)
    for host in workers:
        _join_swarm(host, manager_ip, join_token, log)
    return {"nodes": facts, "manager": manager.alias}


def verifications(ctx: NodeContext) -> list[tuple[str, str]]:
    """What a swarm node runs on top of the node verifications."""
    return [
        ("docker running", "docker info --format '{{.ServerVersion}}'"),
        (
            f"docker at pinned version {versions.DOCKER}",
            f"docker info --format '{{{{.ServerVersion}}}}' | grep -q {versions.DOCKER}",
        ),
        (f"compose at pinned version {versions.COMPOSE}", f"docker compose version | grep -q {versions.COMPOSE}"),
        ("swarm active", "test \"$(docker info --format '{{.Swarm.LocalNodeState}}')\" = active"),
        ("docker trusts and reaches gitea registry", node.registry_verification(ctx)),
        ("container egress hooked", "iptables -C DOCKER-USER -j OUTPOST"),
    ]


def _start_swarm(manager: Host, manager_ip: str, args: str, log) -> str:
    """Make manager a swarm manager, unless it is one already. Returns the token workers join with."""
    if _in_a_swarm(manager):
        log(f"  [{manager.alias}] already a swarm node")
    else:
        manager.run(f"docker swarm init --advertise-addr {manager_ip} {args}".strip(), raise_on_failure=True)
        log(f"  [{manager.alias}] swarm initialised (manager)")
    return manager.run("docker swarm join-token -q worker", raise_on_failure=True).stdout.strip()


def _join_swarm(host: Host, manager_ip: str, join_token: str, log) -> None:
    if _in_a_swarm(host):
        log(f"  [{host.alias}] already joined")
        return
    host.run(f"docker swarm join --token {join_token} {manager_ip}:2377", raise_on_failure=True)
    log(f"  [{host.alias}] joined as worker")


def _in_a_swarm(host: Host) -> bool:
    return host.run(SWARM_STATE, raise_on_failure=True).stdout.strip() == "active"
