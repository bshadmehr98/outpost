"""Pinned versions of the platform pieces and where to download them.

These are the only things Outpost needs that are not in your own needs files.
Bump a version here, run `outpost fetch`, `outpost upload`, and re-run the setup.
"""

from urllib.parse import quote

# One box serves one CPU architecture. `arch:` in servers.yml picks it (default amd64) and the CLI
# calls use_arch() once after reading servers.yml, so every download name below follows.
ARCHES = {
    "amd64": {"uname": "x86_64", "docker": "x86_64", "compose": "x86_64", "k3s": "k3s",
              "pip": ["manylinux2014_x86_64", "manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "linux_x86_64"]},
    "arm64": {"uname": "aarch64", "docker": "aarch64", "compose": "aarch64", "k3s": "k3s-arm64",
              "pip": ["manylinux2014_aarch64", "manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "linux_aarch64"]},
}
ALIASES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
ARCH = "amd64"


def normalize_arch(arch: str) -> str:
    key = ALIASES.get(str(arch).lower())
    if not key:
        raise ValueError(f"unknown arch {arch!r}; use one of {', '.join(sorted(set(ALIASES)))}")
    return key


def use_arch(arch: str) -> str:
    global ARCH
    ARCH = normalize_arch(arch)
    return ARCH


def names(arch: str | None = None) -> dict:
    return ARCHES[arch or ARCH]

GITEA = "28.2.0"
DOCKER = "29.9.2"
COMPOSE = "5.6.1"
BUILDX = "0.38.1"
COREDNS = "1.15.0"
K3S = "v1.37.1+k3s1"
RUNNER = "5.2.2"            # Gitea Actions runner, installed on the first node of each infrastructure


def platform_files(mirrors, infra_types: set[str]) -> list[dict]:
    """Files every platform needs, as plain dicts: name, version, url, filename."""
    n = names()
    docker_static = mirrors.docker_static
    if not docker_static.endswith(("/x86_64", "/aarch64")):   # older mirrors.yml files name the arch themselves
        docker_static = f"{docker_static}/{n['docker']}"
    files = [
        {
            "name": "gitea",
            "version": GITEA,
            "url": f"{mirrors.gitea_dl}/{GITEA}/gitea-{GITEA}-linux-{ARCH}",
            "filename": "gitea",
        }
    ]
    if infra_types:
        files += [
            {
                "name": "coredns",
                "version": COREDNS,
                "url": f"{mirrors.github}/coredns/coredns/releases/download/v{COREDNS}/coredns_{COREDNS}_linux_{ARCH}.tgz",
                "filename": f"coredns_{COREDNS}_linux_{ARCH}.tgz",
            },
            {
                "name": "gitea-runner",
                "version": RUNNER,
                "url": f"{mirrors.gitea_runner_dl}/v{RUNNER}/gitea-runner-{RUNNER}-linux-{ARCH}",
                "filename": "gitea-runner",
            },
        ]
    if infra_types & {"compose", "swarm"}:
        files += [
            {
                "name": "docker",
                "version": DOCKER,
                "url": f"{docker_static}/docker-{DOCKER}.tgz",
                "filename": f"docker-{DOCKER}.tgz",
            },
            {
                "name": "docker-compose",
                "version": COMPOSE,
                "url": f"{mirrors.github}/docker/compose/releases/download/v{COMPOSE}/docker-compose-linux-{n['compose']}",
                "filename": "docker-compose",
            },
            {
                "name": "docker-buildx",
                "version": BUILDX,
                "url": f"{mirrors.github}/docker/buildx/releases/download/v{BUILDX}/buildx-v{BUILDX}.linux-{ARCH}",
                "filename": "docker-buildx",
            },
        ]
    if "k3s" in infra_types:
        tag = quote(K3S, safe="")
        files += [
            {
                "name": "k3s",
                "version": K3S,
                "url": f"{mirrors.github}/k3s-io/k3s/releases/download/{tag}/{n['k3s']}",
                "filename": "k3s",
            },
            {
                "name": "k3s-airgap-images",
                "version": K3S,
                "url": f"{mirrors.github}/k3s-io/k3s/releases/download/{tag}/k3s-airgap-images-{ARCH}.tar.gz",
                "filename": f"k3s-airgap-images-{ARCH}.tar.gz",
            },
            {
                "name": "k3s-install",
                "version": K3S,
                "url": mirrors.k3s_install,
                "filename": "install.sh",
            },
        ]
    return files
