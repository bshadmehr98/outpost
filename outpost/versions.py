"""Pinned versions of the pieces Outpost installs, where to download them, and the CPU type.

These are the only things Outpost needs that are not in your own needs files. Bump a version
here, run `outpost fetch`, `outpost upload`, and re-run the setup.

    use_arch("arm64")                          # once, after reading servers.yml (AirGappedSystem.load does it)
    arch_names()["docker"]                     # aarch64: the CPU type as Docker's downloads name it
    pinned_files(mirrors, {"compose", "k3s"})  # [{"name": "gitea", "version": ..., "url": ..., "filename": ...}, ...]

ARCH is a module global that use_arch() sets: read it as `versions.ARCH` at call time, never import
the name by value.
"""

from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:  # config/servers.py imports this module
    from outpost.config import Mirrors

# One box serves one CPU type. `arch:` in servers.yml picks it (default amd64), and each
# download below is named for it.
ARCHES = {
    "amd64": {
        "uname": "x86_64",
        "docker": "x86_64",
        "compose": "x86_64",
        "k3s": "k3s",
        "pip": ["manylinux2014_x86_64", "manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "linux_x86_64"],
    },
    "arm64": {
        "uname": "aarch64",
        "docker": "aarch64",
        "compose": "aarch64",
        "k3s": "k3s-arm64",
        "pip": ["manylinux2014_aarch64", "manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "linux_aarch64"],
    },
}
ALIASES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
ARCH = "amd64"

GITEA = "28.2.0"
DOCKER = "29.9.2"
COMPOSE = "5.6.1"
BUILDX = "0.38.1"
COREDNS = "1.15.0"
K3S = "v1.37.1+k3s1"
RUNNER = "5.2.2"  # Gitea Actions runner, installed on the first node of each infrastructure


def normalize_arch(arch: str) -> str:
    """x86_64 or amd64 -> amd64; aarch64 or arm64 -> arm64.

    Raises:
        ValueError: an arch Outpost does not support.
    """
    key = ALIASES.get(str(arch).lower())
    if not key:
        raise ValueError(f"unknown arch {arch!r}; use one of {', '.join(sorted(set(ALIASES)))}")
    return key


def use_arch(arch: str) -> str:
    """Make arch the CPU type every download and check uses from now on. Returns its normal name."""
    global ARCH
    ARCH = normalize_arch(arch)
    return ARCH


def arch_names(arch: str | None = None) -> dict:
    """How each download and tool names the CPU type: uname, docker, compose, k3s, and pip's platform tags."""
    return ARCHES[arch or ARCH]


def pinned_files(mirrors: "Mirrors", infra_types: set[str]) -> list[dict]:
    """The pinned downloads the system needs, as dicts with name, version, url and filename.

    Gitea always; coredns and the Actions runner for any infrastructure; Docker, compose and buildx
    for compose and swarm; k3s, its images and its installer for k3s.
    """
    files = _gitea_files(mirrors)
    if infra_types:
        files += _node_files(mirrors)
    if infra_types & {"compose", "swarm"}:
        files += _docker_files(mirrors)
    if "k3s" in infra_types:
        files += _k3s_files(mirrors)
    return files


def _gitea_files(mirrors: "Mirrors") -> list[dict]:
    return [_file("gitea", GITEA, f"{mirrors.gitea_dl}/{GITEA}/gitea-{GITEA}-linux-{ARCH}", "gitea")]


def _node_files(mirrors: "Mirrors") -> list[dict]:
    coredns = f"coredns_{COREDNS}_linux_{ARCH}.tgz"
    return [
        _file("coredns", COREDNS, f"{mirrors.github}/coredns/coredns/releases/download/v{COREDNS}/{coredns}", coredns),
        _file(
            "gitea-runner",
            RUNNER,
            f"{mirrors.gitea_runner_dl}/v{RUNNER}/gitea-runner-{RUNNER}-linux-{ARCH}",
            "gitea-runner",
        ),
    ]


def _docker_files(mirrors: "Mirrors") -> list[dict]:
    docker_static = mirrors.docker_static
    if not docker_static.endswith(("/x86_64", "/aarch64")):  # older mirrors.yml files name the arch themselves
        docker_static = f"{docker_static}/{arch_names()['docker']}"
    releases = f"{mirrors.github}/docker"
    return [
        _file("docker", DOCKER, f"{docker_static}/docker-{DOCKER}.tgz", f"docker-{DOCKER}.tgz"),
        _file(
            "docker-compose",
            COMPOSE,
            f"{releases}/compose/releases/download/v{COMPOSE}/docker-compose-linux-{arch_names()['compose']}",
            "docker-compose",
        ),
        _file(
            "docker-buildx",
            BUILDX,
            f"{releases}/buildx/releases/download/v{BUILDX}/buildx-v{BUILDX}.linux-{ARCH}",
            "docker-buildx",
        ),
    ]


def _k3s_files(mirrors: "Mirrors") -> list[dict]:
    release = f"{mirrors.github}/k3s-io/k3s/releases/download/{quote(K3S, safe='')}"
    images = f"k3s-airgap-images-{ARCH}.tar.gz"
    return [
        _file("k3s", K3S, f"{release}/{arch_names()['k3s']}", "k3s"),
        _file("k3s-airgap-images", K3S, f"{release}/{images}", images),
        _file("k3s-install", K3S, mirrors.k3s_install, "install.sh"),
    ]


def _file(name: str, version: str, url: str, filename: str) -> dict:
    return {"name": name, "version": version, "url": url, "filename": filename}
