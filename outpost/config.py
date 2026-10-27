"""servers.yml and mirrors.yml, loaded into small dataclasses with clear errors."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import versions

INFRA_TYPES = ("compose", "swarm", "k3s")


class ConfigError(Exception):
    """A problem in servers.yml or mirrors.yml, worded for humans."""


@dataclass
class GiteaConfig:
    host: str                      # SSH alias from ~/.ssh/config
    name: str                      # DNS name every node will use, e.g. gitea.internal
    ip: str | None = None          # detected over SSH when missing
    org: str = "pkgs"              # Gitea organisation that holds pip, npm, apt, apk, rpm and files
    user: str = "outpost"          # Gitea admin user Outpost creates
    allow: list[str] = field(default_factory=list)  # extra CIDRs the Gitea host may reach

    @property
    def url(self) -> str:
        return f"https://{self.name}"


@dataclass
class InfraConfig:
    name: str
    type: str                      # compose | swarm | k3s
    nodes: list[str]               # SSH aliases
    allow: list[str] = field(default_factory=list)  # extra CIDRs the nodes may reach
    args: str = ""                 # extra flags for `k3s server` or `docker swarm init`


@dataclass
class Servers:
    gitea: GiteaConfig
    infrastructures: dict[str, InfraConfig]
    ssh_config: Path | None = None  # alternative ssh config file, relative to the platform folder
    arch: str = "amd64"             # CPU architecture of every server: amd64 or arm64

    def infra(self, name: str) -> InfraConfig:
        if name not in self.infrastructures:
            known = ", ".join(self.infrastructures) or "none"
            raise ConfigError(f"No infrastructure named '{name}' in servers.yml (known: {known})")
        return self.infrastructures[name]

    @property
    def infra_types(self) -> set[str]:
        return {i.type for i in self.infrastructures.values()}


@dataclass
class Mirrors:
    pypi: str = "https://pypi.org/simple"
    npm: str = "https://registry.npmjs.org"
    docker: str = "https://registry-1.docker.io"
    debian: str = "http://deb.debian.org/debian"
    debian_security: str = "http://security.debian.org/debian-security"
    ubuntu: str = "http://archive.ubuntu.com/ubuntu"
    alpine: str = "https://dl-cdn.alpinelinux.org/alpine"
    rocky: str = "https://dl.rockylinux.org/pub/rocky"
    alma: str = "https://repo.almalinux.org/almalinux"
    fedora: str = "https://dl.fedoraproject.org/pub/fedora/linux"
    centos_stream: str = "https://mirror.stream.centos.org"
    github: str = "https://github.com"
    gitea_dl: str = "https://dl.gitea.com/gitea"
    docker_static: str = "https://download.docker.com/linux/static/stable"
    k3s_install: str = "https://get.k3s.io"
    gitea_runner_dl: str = "https://gitea.com/gitea/runner/releases/download"


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return data


def load_servers(path: Path) -> Servers:
    if not path.exists():
        raise ConfigError(f"{path} not found. Run `outpost init` to create a platform folder.")
    data = _read_yaml(path)

    g = data.get("gitea")
    if not isinstance(g, dict) or not g.get("host") or not g.get("name"):
        raise ConfigError("servers.yml needs a `gitea:` section with `host:` (SSH alias) and `name:` (DNS name)")
    gitea = GiteaConfig(
        host=str(g["host"]),
        name=str(g["name"]).lower(),
        ip=str(g["ip"]) if g.get("ip") else None,
        org=str(g.get("org", "pkgs")),
        user=str(g.get("user", "outpost")),
        allow=[str(x) for x in g.get("allow", [])],
    )

    infras: dict[str, InfraConfig] = {}
    for name, spec in (data.get("infrastructures") or {}).items():
        if not isinstance(spec, dict):
            raise ConfigError(f"infrastructure '{name}' must be a mapping with `type:` and `nodes:`")
        itype = spec.get("type")
        if itype not in INFRA_TYPES:
            raise ConfigError(f"infrastructure '{name}': type must be one of {', '.join(INFRA_TYPES)} (got {itype!r})")
        nodes = spec.get("nodes") or []
        if not isinstance(nodes, list) or not nodes:
            raise ConfigError(f"infrastructure '{name}': `nodes:` must list at least one SSH alias")
        infras[str(name)] = InfraConfig(
            name=str(name), type=itype, nodes=[str(n) for n in nodes],
            allow=[str(x) for x in spec.get("allow", [])],
            args=str(spec.get("args", "") or ""),
        )
    ssh_config = (path.parent / str(data["ssh_config"])).resolve() if data.get("ssh_config") else None
    try:
        arch = versions.normalize_arch(data.get("arch") or "amd64")
    except ValueError as e:
        raise ConfigError(f"servers.yml: {e}") from e
    return Servers(gitea=gitea, infrastructures=infras, ssh_config=ssh_config, arch=arch)


def load_mirrors(path: Path) -> Mirrors:
    if not path.exists():
        return Mirrors()
    data = _read_yaml(path)
    unknown = set(data) - set(Mirrors.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"mirrors.yml has unknown keys: {', '.join(sorted(unknown))}")
    return Mirrors(**{k: str(v).rstrip("/") for k, v in data.items()})


__all__ = ["ConfigError", "GiteaConfig", "InfraConfig", "Servers", "Mirrors", "load_servers", "load_mirrors", "versions"]
