"""Pinned versions of the pieces Outpost installs, where to download them, and the CPU type.

These are the only things Outpost needs that are not in your own needs files. Bump a version
here, run `outpost fetch`, `outpost upload`, and re-run the setup.

    arch_names("arm64")["docker"]                       # aarch64: the CPU type as Docker's downloads name it
    check_server_arch("dev-1", host.facts(), "arm64")   # RuntimeError when the server is of another CPU type
    pinned_files(mirrors, {"compose", "k3s"}, "amd64")  # [{"name": "gitea", "version": ..., "url": ..., ...}, ...]

The CPU type is never a global: `arch:` in servers.yml is read into Servers.arch, and the system
(AirGappedSystem.arch) passes it to every function that needs it.
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
DEFAULT_ARCH = "amd64"  # when servers.yml has no `arch:`

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


def arch_names(arch: str) -> dict:
    """How each download and tool names the CPU type arch (amd64 or arm64): uname, docker, compose,
    k3s, and pip's platform tags."""
    return ARCHES[arch]


def check_server_arch(alias: str, facts: dict, arch: str) -> None:
    """Stop when a server's CPU type (facts["arch"], from uname) is not arch, the one the box is built for.

    Raises:
        RuntimeError: the server is of another CPU type. Facts without an arch pass.
    """
    wanted = arch_names(arch)["uname"]
    if facts.get("arch") and facts["arch"] != wanted:
        raise RuntimeError(
            f"[{alias}] is {facts['arch']} but this workspace is built for {wanted} (arch: {arch} in servers.yml)."
        )


def pinned_files(mirrors: "Mirrors", infra_types: set[str], arch: str) -> list[dict]:
    """The pinned downloads the system needs, for the CPU type arch, as dicts with name, version, url
    and filename.

    Gitea always; coredns and the Actions runner for any infrastructure; Docker, compose and buildx
    for compose and swarm; k3s, its images and its installer for k3s.
    """
    files = _gitea_files(mirrors, arch)
    if infra_types:
        files += _node_files(mirrors, arch)
    if infra_types & {"compose", "swarm"}:
        files += _docker_files(mirrors, arch)
    if "k3s" in infra_types:
        files += _k3s_files(mirrors, arch)
    return files


def _gitea_files(mirrors: "Mirrors", arch: str) -> list[dict]:
    return [_file("gitea", GITEA, f"{mirrors.gitea_dl}/{GITEA}/gitea-{GITEA}-linux-{arch}", "gitea")]


def _node_files(mirrors: "Mirrors", arch: str) -> list[dict]:
    coredns = f"coredns_{COREDNS}_linux_{arch}.tgz"
    return [
        _file("coredns", COREDNS, f"{mirrors.github}/coredns/coredns/releases/download/v{COREDNS}/{coredns}", coredns),
        _file(
            "gitea-runner",
            RUNNER,
            f"{mirrors.gitea_runner_dl}/v{RUNNER}/gitea-runner-{RUNNER}-linux-{arch}",
            "gitea-runner",
        ),
    ]


def _docker_files(mirrors: "Mirrors", arch: str) -> list[dict]:
    docker_static = mirrors.docker_static
    if not docker_static.endswith(("/x86_64", "/aarch64")):  # older mirrors.yml files name the arch themselves
        docker_static = f"{docker_static}/{arch_names(arch)['docker']}"
    releases = f"{mirrors.github}/docker"
    return [
        _file("docker", DOCKER, f"{docker_static}/docker-{DOCKER}.tgz", f"docker-{DOCKER}.tgz"),
        _file(
            "docker-compose",
            COMPOSE,
            f"{releases}/compose/releases/download/v{COMPOSE}/docker-compose-linux-{arch_names(arch)['compose']}",
            "docker-compose",
        ),
        _file(
            "docker-buildx",
            BUILDX,
            f"{releases}/buildx/releases/download/v{BUILDX}/buildx-v{BUILDX}.linux-{arch}",
            "docker-buildx",
        ),
    ]


def _k3s_files(mirrors: "Mirrors", arch: str) -> list[dict]:
    release = f"{mirrors.github}/k3s-io/k3s/releases/download/{quote(K3S, safe='')}"
    images = f"k3s-airgap-images-{arch}.tar.gz"
    return [
        _file("k3s", K3S, f"{release}/{arch_names(arch)['k3s']}", "k3s"),
        _file("k3s-airgap-images", K3S, f"{release}/{images}", images),
        _file("k3s-install", K3S, mirrors.k3s_install, "install.sh"),
    ]


def _file(name: str, version: str, url: str, filename: str) -> dict:
    return {"name": name, "version": version, "url": url, "filename": filename}
