"""AirGappedSystem: everything Outpost knows about one air-gapped system, in one object.

    system = AirGappedSystem.load("my-system")    # reads servers.yml; raises ConfigError
    system.servers.gitea.name                     # the config
    system.state("upload")                        # state/upload.json, read now
    system.secret("gitea-token")                  # secrets/gitea-token, read now
    system.gitea_host.run("uptime")               # a Host, made once per alias
    for host in system.nodes("dev"): ...

Commands load one and pass it on, instead of passing the workspace, the config and the secrets one
by one. It holds and reads things; it decides nothing. How nodes reach Gitea and the egress proxy
takes rules, so endpoints.py works that out.

Tests replace `outpost.system.Host` to give every command a FakeHost.
"""

from functools import cached_property
from pathlib import Path

from outpost import ssh, versions
from outpost.config import InfraConfig, Mirrors, Servers, load_mirrors, load_servers
from outpost.ssh import Host
from outpost.workspace import STATE_GITEA, Workspace, infra_state


class AirGappedSystem:
    """Everything Outpost knows about one air-gapped system: its workspace, config, state and secrets."""

    def __init__(self, workspace: Workspace, servers: Servers):
        self.workspace = workspace
        self.servers = servers
        self._hosts: dict[str, Host] = {}

    @classmethod
    def load(cls, root: str | Path) -> "AirGappedSystem":
        """Open the workspace at root and read its servers.yml.

        It also points every ssh call at servers.yml's ssh_config and every download at its arch.

        Raises:
            ConfigError: root has no servers.yml, or servers.yml is wrong.
        """
        workspace = Workspace.open(root)
        servers = load_servers(workspace.servers_file)
        ssh.use_config(servers.ssh_config)
        versions.use_arch(servers.arch)
        return cls(workspace, servers)

    @cached_property
    def mirrors(self) -> Mirrors:
        """mirrors.yml (the defaults when it is missing), read on first use: only needs and fetch use it, so a
        broken mirrors.yml does not stop the other commands."""
        return load_mirrors(self.workspace.mirrors_file)

    # -- state and secrets, read from disk on every call ------------------------------
    def state(self, name: str, default=None):
        """The content of state/<name>.json, or default."""
        return self.workspace.read_state(name, default)

    def write_state(self, name: str, data) -> Path:
        """Write state/<name>.json."""
        return self.workspace.write_state(name, data)

    def secret(self, name: str) -> str | None:
        """The content of secrets/<name>, or None."""
        return self.workspace.read_secret(name)

    def write_secret(self, name: str, value: str) -> Path:
        """Write secrets/<name>, mode 600."""
        return self.workspace.write_secret(name, value)

    def ca_pem(self) -> str:
        """The text of pki/ca.crt.

        Raises:
            RuntimeError: the CA does not exist yet.
        """
        ca = self.workspace.ca_cert
        if not ca.exists():
            raise RuntimeError("pki/ca.crt is missing. Run `outpost gitea setup` first; it creates the CA.")
        return ca.read_text()

    # -- servers ---------------------------------------------------------------------
    def host(self, alias: str) -> Host:
        """The Host for an ssh alias, made on first use and then kept."""
        if alias not in self._hosts:
            self._hosts[alias] = Host(alias)
        return self._hosts[alias]

    @property
    def gitea_host(self) -> Host:
        return self.host(self.servers.gitea.host)

    @property
    def proxy_host(self) -> Host | None:
        """The egress proxy's server, or None when servers.yml has no egress_proxy."""
        proxy = self.servers.egress_proxy
        return self.host(proxy.host) if proxy else None

    def infra(self, name: str) -> InfraConfig:
        """The infrastructure called name. Raises ConfigError for an unknown name."""
        return self.servers.infra(name)

    def nodes(self, infra_name: str) -> list[Host]:
        """The Hosts of one infrastructure's nodes. Raises ConfigError for an unknown name."""
        return [self.host(alias) for alias in self.infra(infra_name).nodes]

    def set_up_infras(self) -> list[InfraConfig]:
        """The infrastructures `outpost infra setup` has run on (they have a state/infra-<name>.json)."""
        return [infra for name, infra in self.servers.infrastructures.items() if self.state(infra_state(name))]

    def gitea_is_set_up(self) -> bool:
        """True once `outpost gitea setup` has recorded state/gitea.json."""
        return bool(self.state(STATE_GITEA))
