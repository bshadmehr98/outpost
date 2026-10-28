"""`outpost infra setup` and `outpost infra verify`: one infrastructure, from its servers.yml entry.

set_up_infra(system, system.infra("dev"), log=print)    # every node, the runtime, the runner, then verify
verify_infra(system, system.infra("dev"), log=print)    # read-only
"""

from outpost import hosts
from outpost.config import InfraConfig
from outpost.infra import compose, k3s, runner, swarm
from outpost.infra.context import NodeContext, node_context
from outpost.infra.proxy import proxy_verifications
from outpost.infra.verify import log_verifications, node_verifications, run_verifications
from outpost.system import AirGappedSystem
from outpost.workspace import infra_state, verify_state

# One module per infrastructure type, each with setup(ctx, hosts, log) and verifications(ctx).
TYPES = {"compose": compose, "swarm": swarm, "k3s": k3s}


def set_up_infra(system: AirGappedSystem, infra: InfraConfig, log=print) -> bool:
    """Set up every node of an infrastructure, install the runner on the first, record it, then verify.

    Returns:
        True when every verification passed.

    Raises:
        RuntimeError: Gitea is not set up, or a step failed on a node.
        SshError: a node could not be reached.
    """
    ctx = node_context(system, infra)
    nodes = system.nodes(infra.name)
    log(f"Setting up {infra.name} ({infra.type}) on {', '.join(infra.nodes)}")
    result = TYPES[infra.type].setup(ctx, nodes, log=log)
    result["runner"] = runner.install_runner_for(system, ctx, nodes[0], log=log)
    system.write_state(infra_state(infra.name), {"type": infra.type, **result})
    log()
    return _verify(system, infra, ctx, log)


def verify_infra(system: AirGappedSystem, infra: InfraConfig, log=print) -> bool:
    """Run the verifications on every node of an infrastructure and record them. True when all passed."""
    return _verify(system, infra, node_context(system, infra), log)


def verifications_for(infra_type: str, ctx: NodeContext) -> list[tuple[str, str]]:
    """Every verification a node of this type runs: the node's, the egress proxy's, the type's, the runner's."""
    return (
        node_verifications(ctx)
        + proxy_verifications(ctx)
        + TYPES[infra_type].verifications(ctx)
        + runner.verifications()
    )


def _verify(system: AirGappedSystem, infra: InfraConfig, ctx: NodeContext, log) -> bool:
    """Verify each node, log it, record what it runs; then record the whole in state/verify-<name>.json."""
    log(f"==> verifying {infra.name}")
    verifications = verifications_for(infra.type, ctx)
    all_passed = True
    report = {}
    for node in system.nodes(infra.name):
        results = run_verifications(node, verifications)
        report[node.alias] = results
        all_passed &= log_verifications(node.alias, results, log)
        hosts.record(system.workspace, node.alias, hosts.inspect(node))
    system.write_state(verify_state(infra.name), {"result": "ok" if all_passed else "problems", "nodes": report})
    return all_passed
