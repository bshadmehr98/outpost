"""A lab: throwaway servers in Docker, for seeing a full run without owning any VPS.

  outpost lab up        build an Ubuntu image with systemd and sshd, start it as servers on a
                        private Docker network, write lab/ssh_config and servers.yml for them
  outpost lab down      remove the containers, network and volumes

The servers are ordinary containers that believe they are machines: systemd as PID 1, sshd, root
login with a key generated into lab/. From then on every other command works unchanged, over SSH,
exactly as it would against real servers. The integration tests use the same lab.
"""

import shutil
import subprocess
import time
from pathlib import Path

from outpost.workspace import GITEA_ADMIN_PASSWORD, GITEA_TOKEN, Workspace

IMAGE = "outpost-lab-server"
NETWORK, SUBNET = "outpost-lab", "172.31.252.0/24"
DOCKERFILE = Path(__file__).parent / "templates" / "lab" / "Dockerfile"

# alias -> (ip, extra docker run flags). Docker-in-Docker and k3s want their data on a volume.
SERVERS = {
    "lab-gitea": ("172.31.252.10", []),
    "lab-node-1": ("172.31.252.21", ["-v", "lab-node-1-docker:/var/lib/docker"]),
    "lab-node-2": ("172.31.252.22", ["-v", "lab-node-2-docker:/var/lib/docker"]),
    "lab-k3s": ("172.31.252.30", ["-v", "lab-k3s-rancher:/var/lib/rancher", "--tmpfs", "/var/run"]),
}
VOLUMES = ["lab-node-1-docker", "lab-node-2-docker", "lab-k3s-rancher"]

SERVERS_YML = """\
# Written by `outpost lab up`. The hosts are Docker containers on this machine.
ssh_config: lab/ssh_config

gitea:
  host: lab-gitea
  name: gitea.lab
  ip: {gitea_ip}

infrastructures:
  dev:
    type: compose
    nodes: [lab-node-1]
  prod:
    type: swarm
    nodes: [lab-node-1, lab-node-2]
{k3s}"""

# The kubelet's default is "evict when less than 10% of the disk is free", which a laptop often is.
K3S_YML = """\
  k8s:
    type: k3s
    nodes: [lab-k3s]
    args: '--kubelet-arg=eviction-hard=nodefs.available<1Gi,imagefs.available<1Gi'
"""


def servers_yml(k3s: bool = True) -> str:
    """The servers.yml `lab up` writes: Gitea, a compose and a swarm infrastructure, and k3s if asked."""
    return SERVERS_YML.format(gitea_ip=SERVERS["lab-gitea"][0], k3s=K3S_YML if k3s else "")


def ssh_config(key: Path, aliases) -> str:
    """lab/ssh_config: root login with the lab's key, and no host key checks (the servers are remade often)."""
    return "".join(
        f"Host {alias}\n  HostName {SERVERS[alias][0]}\n  User root\n  IdentityFile {key}\n"
        "  StrictHostKeyChecking no\n  UserKnownHostsFile /dev/null\n  LogLevel ERROR\n"
        for alias in aliases
    )


def up(workspace: Workspace, k3s: bool = True, log=print) -> list[str]:
    """Start the lab servers (again, from scratch), write lab/ssh_config and servers.yml for them, and
    wait until each one accepts ssh.

    Args:
        k3s: also start lab-k3s, the k3s server (about 1 GB of memory).

    Returns:
        The aliases of the servers started.

    Raises:
        RuntimeError: Docker is missing, or a server did not start accepting ssh.
    """
    if not shutil.which("docker"):
        raise RuntimeError("The lab needs Docker on this machine.")
    aliases = [alias for alias in SERVERS if k3s or alias != "lab-k3s"]
    workspace.lab_dir.mkdir(parents=True, exist_ok=True)
    key = _ensure_ssh_key(workspace.lab_dir)
    _build_image(workspace.lab_dir, key, log)
    down(workspace, log=lambda *_: None)
    _start_servers(aliases, log)
    _write_lab_config(workspace, key, aliases, k3s)
    _forget_old_servers(workspace)
    _wait_for_ssh(workspace.lab_dir / "ssh_config", aliases)
    log(f"wrote servers.yml and lab/ssh_config; `ssh -F lab/ssh_config {aliases[0]}` gets you a shell")
    return aliases


def down(workspace: Workspace, log=print) -> None:
    """Remove the lab's containers, network and volumes. servers.yml and lab/ stay."""
    _docker("rm", "-f", *SERVERS, check=False)
    _docker("network", "rm", NETWORK, check=False)
    _docker("volume", "rm", *VOLUMES, check=False)
    log("lab containers, network and volumes removed; servers.yml and lab/ are kept")


# -- the steps of up() ------------------------------------------------------------------------


def _ensure_ssh_key(lab: Path) -> Path:
    """lab/id_ed25519, made once; the servers let it log in as root."""
    key = lab / "id_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


def _build_image(lab: Path, key: Path, log) -> None:
    """Build the server image from templates/lab/Dockerfile, with the public key as authorized_keys."""
    build = lab / "build"
    build.mkdir(exist_ok=True)
    shutil.copy(DOCKERFILE, build / "Dockerfile")
    shutil.copy(key.with_suffix(".pub"), build / "authorized_keys")
    log("building the server image (Ubuntu 24.04 with systemd and sshd)")
    _docker("build", "-q", "-t", IMAGE, str(build))


def _start_servers(aliases: list[str], log) -> None:
    """The lab network, and one privileged container per server, with systemd as PID 1."""
    _docker("network", "create", "--subnet", SUBNET, NETWORK)
    for alias in aliases:
        ip, extra = SERVERS[alias]
        _docker(
            "run",
            "-d",
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            "--tmpfs",
            "/tmp",
            "--name",
            alias,
            "--hostname",
            alias,
            "--network",
            NETWORK,
            "--ip",
            ip,
            *extra,
            IMAGE,
        )
        log(f"started {alias} at {ip}")


def _write_lab_config(workspace: Workspace, key: Path, aliases: list[str], k3s: bool) -> None:
    (workspace.lab_dir / "ssh_config").write_text(ssh_config(key, aliases))
    workspace.servers_file.write_text(servers_yml(k3s))


def _forget_old_servers(workspace: Workspace) -> None:
    """The servers are brand new, so what Outpost recorded about the old ones is wrong: the state, the
    cached facts in state/hosts/ (else the dashboard and the report show the old versions and seal),
    and Gitea's token and admin password."""
    for stale in [
        workspace.secrets_dir / GITEA_TOKEN,
        workspace.secrets_dir / GITEA_ADMIN_PASSWORD,
        *workspace.state_dir.glob("*.json"),
        *workspace.hosts_state_dir.glob("*.json"),
    ]:
        stale.unlink(missing_ok=True)


def _wait_for_ssh(ssh_config_file: Path, aliases: list[str], seconds: int = 60) -> None:
    """Wait until every server accepts ssh.

    Raises:
        RuntimeError: a server did not, within about `seconds`.
    """
    for alias in aliases:
        for _ in range(seconds):
            if _accepts_ssh(ssh_config_file, alias):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"{alias} did not start accepting SSH; see `docker logs {alias}`")


def _accepts_ssh(ssh_config_file: Path, alias: str) -> bool:
    login = ["ssh", "-F", str(ssh_config_file), "-o", "ConnectTimeout=2", alias, "true"]
    return subprocess.run(login, capture_output=True).returncode == 0


def _docker(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)
