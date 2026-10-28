"""`outpost infra setup` and `outpost infra verify`: one infrastructure, from its servers.yml entry.

    set_up_infra(system, system.infra("dev"), log=print)    # every node, the runtime, the runner, then verify
    verify_infra(system, system.infra("dev"), log=print)    # read-only

infra/__init__.py does not import this module: it imports gitea, which still imports infra.egress.
"""

from outpost import hosts
from outpost.infra import TYPES
from outpost.infra.context import NodeContext, node_context
from outpost.infra.runner import extra_checks as runner_extra_checks
from outpost.infra.runner import install_runner_for
from outpost.infra.verify import log_verifications, verify_node
from outpost.system import AirGappedSystem
from outpost.workspace import infra_state, verify_state


def set_up_infra(system: AirGappedSystem, infra, log=print) -> bool:
    """Set up every node of an infrastructure (an InfraConfig), install the runner on the first, verify.

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
    result["runner"] = install_runner_for(system, ctx, nodes[0], log=log)
    system.write_state(infra_state(infra.name), {"type": infra.type, **result})
    log()
    return _verify(system, infra, ctx, log)


def verify_infra(system: AirGappedSystem, infra, log=print) -> bool:
    """Run the verifications on every node of an infrastructure and record them. True when all passed."""
    return _verify(system, infra, node_context(system, infra), log)


def _verify(system: AirGappedSystem, infra, ctx: NodeContext, log) -> bool:
    log(f"==> verifying {infra.name}")
    verifications = TYPES[infra.type].extra_checks(ctx) + runner_extra_checks()
    all_passed = True
    report = {}
    for node in system.nodes(infra.name):
        results = verify_node(node, ctx, verifications)
        report[node.alias] = results
        all_passed &= log_verifications(node.alias, results, log)
        hosts.record(system.workspace, node.alias, hosts.inspect(node))
    system.write_state(verify_state(infra.name), {"result": "ok" if all_passed else "problems", "nodes": report})
    return all_passed
