"""What the user writes by hand to describe the system: servers.yml and mirrors.yml.

    servers = load_servers(workspace.servers_file)     # raises ConfigError, worded for humans
    servers.gitea.host, servers.infra("dev").nodes, servers.egress_proxy
    mirrors = load_mirrors(workspace.mirrors_file)     # the defaults when the file is missing

Outpost reads the config and never changes it (only the dashboard edits it, for the user).

    errors.py         ConfigError
    yaml_file.py      read_yaml_mapping(): a YAML file whose top level is a mapping
    servers.py        GiteaConfig, InfraConfig, Servers, load_servers(), INFRA_TYPES
    egress_proxy.py   ProxyConfig, load_egress_proxy(), proxy_allow_entry(), HOSTNAME
    mirrors.py        Mirrors, load_mirrors()
"""

from outpost.config.egress_proxy import HOSTNAME, ProxyConfig, load_egress_proxy, proxy_allow_entry
from outpost.config.errors import ConfigError
from outpost.config.mirrors import Mirrors, load_mirrors
from outpost.config.servers import INFRA_TYPES, GiteaConfig, InfraConfig, Servers, load_servers
from outpost.config.yaml_file import read_yaml_mapping

__all__ = [
    "HOSTNAME",
    "INFRA_TYPES",
    "ConfigError",
    "GiteaConfig",
    "InfraConfig",
    "Mirrors",
    "ProxyConfig",
    "Servers",
    "load_egress_proxy",
    "load_mirrors",
    "load_servers",
    "proxy_allow_entry",
    "read_yaml_mapping",
]
