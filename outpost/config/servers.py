"""servers.yml: which server runs Gitea, the infrastructures and their nodes, and the egress proxy."""

from dataclasses import dataclass, field
from pathlib import Path

from outpost import versions
from outpost.config.egress_proxy import ProxyConfig, load_egress_proxy
from outpost.config.errors import ConfigError
from outpost.config.yaml_file import read_yaml_mapping

INFRA_TYPES = ("compose", "swarm", "k3s")


@dataclass
class GiteaConfig:
    """The `gitea:` section: the Gitea server and how nodes name it."""

    host: str  # ssh alias from ~/.ssh/config
    name: str  # DNS name every node will use, e.g. gitea.internal
    ip: str | None = None  # detected over ssh when missing
    org: str = "pkgs"  # Gitea organisation that holds pip, npm, apt, apk, rpm and files
    user: str = "outpost"  # Gitea admin user Outpost creates
    allow: list[str] = field(default_factory=list)  # extra CIDRs the Gitea server may reach

    @property
    def url(self) -> str:
        return f"https://{self.name}"


@dataclass
class InfraConfig:
    """One entry under `infrastructures:`."""

    name: str
    type: str  # compose | swarm | k3s
    nodes: list[str]  # ssh aliases
    allow: list[str] = field(default_factory=list)  # extra CIDRs the nodes may reach
    args: str = ""  # extra flags for `k3s server` or `docker swarm init`


@dataclass
class Servers:
    """All of servers.yml."""

    gitea: GiteaConfig
    infrastructures: dict[str, InfraConfig]
    ssh_config: Path | None = None  # alternative ssh config file, relative to the workspace
    arch: str = "amd64"  # CPU architecture of every server: amd64 or arm64
    egress_proxy: ProxyConfig | None = None  # the named exceptions, when servers.yml has an egress_proxy section

    def infra(self, name: str) -> InfraConfig:
        """The infrastructure called name.

        Raises:
            ConfigError: servers.yml has no infrastructure by that name.
        """
        if name not in self.infrastructures:
            known = ", ".join(self.infrastructures) or "none"
            raise ConfigError(f"No infrastructure named '{name}' in servers.yml (known: {known})")
        return self.infrastructures[name]

    @property
    def infra_types(self) -> set[str]:
        return {infra.type for infra in self.infrastructures.values()}


def load_servers(path: Path) -> Servers:
    """Read and check servers.yml.

    Raises:
        ConfigError: the file is missing, or something in it is wrong; the message says what.
    """
    if not path.exists():
        raise ConfigError(f"{path} not found. Run `outpost init` to create a workspace.")
    data = read_yaml_mapping(path)
    gitea = _load_gitea(data.get("gitea"))
    return Servers(
        gitea=gitea,
        infrastructures=_load_infrastructures(data.get("infrastructures") or {}),
        ssh_config=(path.parent / str(data["ssh_config"])).resolve() if data.get("ssh_config") else None,
        arch=_load_arch(data.get("arch")),
        egress_proxy=load_egress_proxy(data.get("egress_proxy"), gitea.host),
    )


def _load_gitea(section) -> GiteaConfig:
    if not isinstance(section, dict) or not section.get("host") or not section.get("name"):
        raise ConfigError("servers.yml needs a `gitea:` section with `host:` (SSH alias) and `name:` (DNS name)")
    return GiteaConfig(
        host=str(section["host"]),
        name=str(section["name"]).lower(),
        ip=str(section["ip"]) if section.get("ip") else None,
        org=str(section.get("org", "pkgs")),
        user=str(section.get("user", "outpost")),
        allow=[str(cidr) for cidr in section.get("allow", [])],
    )


def _load_infrastructures(section: dict) -> dict[str, InfraConfig]:
    return {str(name): _load_infra(str(name), spec) for name, spec in section.items()}


def _load_infra(name: str, spec) -> InfraConfig:
    if not isinstance(spec, dict):
        raise ConfigError(f"infrastructure '{name}' must be a mapping with `type:` and `nodes:`")
    infra_type = spec.get("type")
    if infra_type not in INFRA_TYPES:
        raise ConfigError(f"infrastructure '{name}': type must be one of {', '.join(INFRA_TYPES)} (got {infra_type!r})")
    nodes = spec.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        raise ConfigError(f"infrastructure '{name}': `nodes:` must list at least one SSH alias")
    return InfraConfig(
        name=name,
        type=infra_type,
        nodes=[str(node) for node in nodes],
        allow=[str(cidr) for cidr in spec.get("allow", [])],
        args=str(spec.get("args", "") or ""),
    )


def _load_arch(value) -> str:
    try:
        return versions.normalize_arch(value or "amd64")
    except ValueError as e:
        raise ConfigError(f"servers.yml: {e}") from e
