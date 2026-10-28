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

from .paths import Platform

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
    return SERVERS_YML.format(gitea_ip=SERVERS["lab-gitea"][0], k3s=K3S_YML if k3s else "")


def ssh_config(key: Path, aliases) -> str:
    return "".join(
        f"Host {alias}\n  HostName {SERVERS[alias][0]}\n  User root\n  IdentityFile {key}\n"
        "  StrictHostKeyChecking no\n  UserKnownHostsFile /dev/null\n  LogLevel ERROR\n"
        for alias in aliases
    )


def _docker(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def up(platform: Platform, k3s: bool = True, log=print) -> list[str]:
    if not shutil.which("docker"):
        raise RuntimeError("The lab needs Docker on this machine.")
    aliases = [a for a in SERVERS if k3s or a != "lab-k3s"]
    lab = platform.root / "lab"
    lab.mkdir(parents=True, exist_ok=True)

    key = lab / "id_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    build = lab / "build"
    build.mkdir(exist_ok=True)
    shutil.copy(DOCKERFILE, build / "Dockerfile")
    shutil.copy(key.with_suffix(".pub"), build / "authorized_keys")
    log("building the server image (Ubuntu 24.04 with systemd and sshd)")
    _docker("build", "-q", "-t", IMAGE, str(build))

    down(platform, log=lambda *_: None)
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

    (lab / "ssh_config").write_text(ssh_config(key, aliases))
    platform.servers_file.write_text(servers_yml(k3s))
    # the servers are brand new, so what Outpost remembered about the old ones is wrong
    for stale in [
        platform.secrets_dir / "gitea-token",
        platform.secrets_dir / "gitea-admin-password",
        *platform.state_dir.glob("*.json"),
    ]:
        stale.unlink(missing_ok=True)

    for alias in aliases:
        for _ in range(60):
            if (
                subprocess.run(
                    ["ssh", "-F", str(lab / "ssh_config"), "-o", "ConnectTimeout=2", alias, "true"], capture_output=True
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"{alias} did not start accepting SSH; see `docker logs {alias}`")
    log(f"wrote servers.yml and lab/ssh_config; `ssh -F lab/ssh_config {aliases[0]}` gets you a shell")
    return aliases


def down(platform: Platform, log=print) -> None:
    _docker("rm", "-f", *SERVERS, check=False)
    _docker("network", "rm", NETWORK, check=False)
    _docker("volume", "rm", *VOLUMES, check=False)
    log("lab containers, network and volumes removed; servers.yml and lab/ are kept")
