"""mirrors.yml: where fetch downloads from. Every key is optional; the defaults are the public ones."""

from dataclasses import dataclass
from pathlib import Path

from outpost.config.errors import ConfigError
from outpost.config.yaml_file import read_yaml_mapping


@dataclass
class Mirrors:
    """The base URL of every place fetch downloads from."""

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


def load_mirrors(path: Path) -> Mirrors:
    """The mirrors in mirrors.yml, or the defaults when the file is missing.

    Raises:
        ConfigError: the file is not a YAML mapping, or has a key Mirrors does not know.
    """
    if not path.exists():
        return Mirrors()
    data = read_yaml_mapping(path)
    unknown = set(data) - set(Mirrors.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"mirrors.yml has unknown keys: {', '.join(sorted(unknown))}")
    return Mirrors(**{key: str(url).rstrip("/") for key, url in data.items()})
