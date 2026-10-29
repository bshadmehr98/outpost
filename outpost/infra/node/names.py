"""Step 1 of a node: know Gitea (and the egress proxy on its own server) by name, through /etc/hosts."""

from outpost import hostsfile
from outpost.infra.context import NodeContext
from outpost.ssh import Host


def write_hosts_entries(host: Host, ctx: NodeContext) -> None:
    """Write the names into /etc/hosts now, and install the unit that puts them back at every boot
    (cloud-init and Docker both rewrite /etc/hosts)."""
    entries = ctx.hosts_entries()
    host.run(hostsfile.rewrite_script(entries), raise_on_failure=True)
    hostsfile.keep_names(host, entries)
