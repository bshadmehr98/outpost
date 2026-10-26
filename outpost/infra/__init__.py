"""Infrastructure types. Each is one module with setup(ctx, hosts, log) and extra_checks(ctx).

  compose   Docker + Compose plugin on each node, nodes are independent
  swarm     Docker on each node, first node initialises the swarm, others join
  k3s       first node is the k3s server, others join as agents
"""

from . import compose, k3s, swarm

TYPES = {"compose": compose, "swarm": swarm, "k3s": k3s}
