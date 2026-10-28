"""Set up, verify and upgrade the nodes of an infrastructure, and the egress proxy.

    set_up_infra(system, system.infra("dev"), log=print)   # `outpost infra setup dev`: True when verified
    verify_infra(system, system.infra("dev"), log=print)   # `outpost infra verify dev`
    upgrade_nodes(system, system.nodes("dev"), log=print)  # `outpost infra upgrade dev`
    proxy.set_up_egress_proxy(system, log=print)          # `outpost proxy setup`

One module per infrastructure type, each with setup(ctx, hosts, log) and verifications(ctx):

    compose   Docker + Compose plugin on each node, nodes are independent
    swarm     Docker on each node, first node initialises the swarm, others join
    k3s       first node is the k3s server, others join as agents

Every node first goes through node.prepare_node (names, client files, trust, OS packages, seal, DNS),
and the first node of each infrastructure gets an Actions runner. A NodeContext (context.py) carries
what every step needs, worked out once from the system.

    setup.py      set_up_infra, verify_infra, verifications_for, TYPES
    upgrade.py    upgrade_nodes, UPGRADE_SCRIPT
    context.py    NodeContext, node_context, proxy_host_context, egress_proxy_config
    node/         prepare_node and the steps every node goes through (see node/__init__.py)
    compose.py    swarm.py    k3s.py    one infrastructure type each
    runner.py     the Actions runner: install_runner_for, install, labels_for
    verify.py     node_verifications, run_verifications, log_verifications, verification_script, by_family
    proxy/        the egress proxy (see proxy/__init__.py)

Code outside this folder uses only the names below.
"""

from outpost.infra import proxy
from outpost.infra.context import NodeContext, node_context, proxy_host_context
from outpost.infra.setup import TYPES, set_up_infra, verifications_for, verify_infra
from outpost.infra.upgrade import UPGRADE_SCRIPT, upgrade_nodes
from outpost.infra.verify import log_verifications

__all__ = [
    "TYPES",
    "UPGRADE_SCRIPT",
    "NodeContext",
    "log_verifications",
    "node_context",
    "proxy",
    "proxy_host_context",
    "set_up_infra",
    "upgrade_nodes",
    "verifications_for",
    "verify_infra",
]
