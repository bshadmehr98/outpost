"""What each server runs right now: one ssh round trip per server, cached as state/hosts/<alias>.json.

    facts = inspect_server(host)                 # OS, address, uptime, disk, versions, seal, workloads
    inspect_and_record(system, host)             # the same, and cache it
    refresh_facts(system, ["dev-1"], log=say)    # inspect some servers (or all of servers.yml), cache, log a line each
    cached_facts(workspace)                      # {alias: facts}: never talks to a server
    server_roles(servers)                        # {"gitea-1": ["gitea"], "dev-1": ["dev: compose"], ...}
    version_drift(facts)                         # ["docker 29.0.0 (pinned 29.9.2)"]

The setup and verify commands record the servers they touched, so the dashboard's Machines page and
the report are current without asking. `outpost hosts` prints the same table; `--refresh` asks every
server again.

    inspect.py      FACTS_SCRIPT, INSTALLED, inspect_server()
    cache.py        record_facts(), cached_facts(), inspect_and_record(), refresh_facts()
    roles.py        server_roles()
    drift.py        pinned_versions(), version_drift()
"""

from outpost.hosts.cache import cached_facts, inspect_and_record, record_facts, refresh_facts
from outpost.hosts.drift import pinned_versions, version_drift
from outpost.hosts.inspect import FACTS_SCRIPT, INSTALLED, inspect_server
from outpost.hosts.roles import server_roles

__all__ = [
    "FACTS_SCRIPT",
    "INSTALLED",
    "cached_facts",
    "inspect_and_record",
    "inspect_server",
    "pinned_versions",
    "record_facts",
    "refresh_facts",
    "server_roles",
    "version_drift",
]
