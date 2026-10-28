"""What is on each server right now: one SSH round trip per host, cached as state/hosts/<alias>.json.

  inspect(host)                      run one script: OS, address, uptime, disk, memory, installed versions,
                                     services, seal state, and the workloads running there
  refresh(platform, servers, ...)    inspect every host in servers.yml (or some) and cache the answers
  roles(servers)                     alias -> ["gitea", "dev: compose", "prod: swarm manager", "egress proxy", ...]
  cached(platform)                   the cache, for the dashboard and the report; never talks to a server

The setup and verify commands refresh the hosts they touched, so the dashboard's Machines page is
current without asking. `outpost hosts` prints the same table; `--refresh` asks every server again.
"""

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path

from . import versions
from .config import Servers
from .paths import Platform
from .ssh import Host, SshError

SCRIPT = r"""
. /etc/os-release 2>/dev/null
echo "id=$ID"; echo "id_like=${ID_LIKE:-}"; echo "version_id=${VERSION_ID:-}"; echo "codename=${VERSION_CODENAME:-}"; echo "pretty=$PRETTY_NAME"
echo "hostname=$(hostname)"; echo "ip=$(hostname -I 2>/dev/null | awk '{print $1}')"; echo "arch=$(uname -m)"
echo "kernel=$(uname -r)"
echo "uptime=$(uptime -p 2>/dev/null | sed 's/^up //')"
echo "booted=$(uptime -s 2>/dev/null)"
echo "disk_free=$(df -h / | awk 'NR==2{print $4}')"; echo "disk_total=$(df -h / | awk 'NR==2{print $2}')"; echo "disk_pct=$(df / | awk 'NR==2{print $5}' | tr -d %)"
echo "mem_total=$(free -m | awk '/Mem/{print $2}')"; echo "mem_used=$(free -m | awk '/Mem/{print $3}')"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "cpus=$(nproc 2>/dev/null)"
echo "egress=$(test -x /usr/local/sbin/outpost-egress && /usr/local/sbin/outpost-egress status || echo 'not installed')"
echo "egress_boot=$(systemctl is-enabled outpost-egress 2>/dev/null || echo no)"
echo "egress_last=$(tail -n 1 /var/log/outpost-egress.log 2>/dev/null)"
echo "gitea=$(/usr/local/bin/gitea --version 2>/dev/null | awk '{print $3}')"
echo "docker=$(docker --version 2>/dev/null | sed -E 's/.*version ([^,]+),.*/\1/')"
echo "compose=$(docker compose version --short 2>/dev/null)"
echo "buildx=$(docker buildx version 2>/dev/null | awk '{print $2}' | sed 's/^v//')"
echo "k3s=$(k3s --version 2>/dev/null | head -1 | awk '{print $3}')"
echo "coredns=$(/usr/local/bin/coredns -version 2>/dev/null | head -1 | sed 's/^CoreDNS-//')"
echo "runner=$(/usr/local/bin/gitea-runner --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
echo "tinyproxy=$(tinyproxy -v 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
if [ -f /var/log/tinyproxy/tinyproxy.log ]; then echo "proxy_log_tail=$(tail -n 20 /var/log/tinyproxy/tinyproxy.log | base64 | tr -d "\\n")"; fi
for s in gitea docker k3s k3s-agent outpost-dns outpost-runner outpost-proxy; do echo "svc_$s=$(systemctl is-active $s 2>/dev/null)"; done
echo "containers=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
echo "compose_projects=$(docker ps --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | grep -v '^$' | sort -u | tr '\n' ' ')"
echo "swarm=$(docker info --format '{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}' 2>/dev/null)"
echo "stacks=$(docker stack ls --format '{{.Name}}' 2>/dev/null | tr '\n' ' ')"
echo "swarm_services=$(docker service ls --format '{{.Name}} {{.Replicas}}' 2>/dev/null | tr '\n' ',')"
echo "deployments=$(k3s kubectl get deploy -A --no-headers 2>/dev/null | awk '{print $1"/"$2" "$3}' | tr '\n' ',')"
echo "pods=$(k3s kubectl get pods -A --no-headers 2>/dev/null | grep -c Running)"
echo "gitea_repos=$(ls /var/lib/gitea/data/repositories/*/ 2>/dev/null | grep -c '\.git$')"
echo "gitea_packages_mb=$(du -sm /var/lib/gitea/data/packages 2>/dev/null | cut -f1)"
"""

INSTALLED = ("gitea", "docker", "compose", "buildx", "k3s", "coredns", "runner", "tinyproxy")


def expected_versions() -> dict:
    """Pinned versions. tinyproxy is not pinned: it is the distro's own package, at whatever version Gitea has."""
    return {
        "gitea": versions.GITEA,
        "docker": versions.DOCKER,
        "compose": versions.COMPOSE,
        "buildx": versions.BUILDX,
        "k3s": versions.K3S,
        "coredns": versions.COREDNS,
        "runner": versions.RUNNER,
    }


def inspect(host) -> dict:
    """Facts from one host. Never raises: an unreachable host comes back with reachable=False and why."""
    try:
        out = host.run(SCRIPT, timeout=90)
    except SshError as e:
        return {"reachable": False, "error": str(e).strip().splitlines()[-1][:300]}
    if not out.stdout.strip():
        return {"reachable": False, "error": (out.stderr.strip().splitlines() or ["no answer"])[-1][:300]}
    data: dict = {"reachable": True}
    for line in out.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    data["installed"] = {k: data.pop(k, "") for k in INSTALLED}
    if "proxy_log_tail" in data:  # base64 on the wire, so a log line cannot break the key=value format
        try:
            data["proxy_log_tail"] = base64.b64decode(data["proxy_log_tail"]).decode("utf-8", "replace").splitlines()
        except ValueError:
            data["proxy_log_tail"] = []
    data["services"] = {k[4:]: data.pop(k) for k in list(data) if k.startswith("svc_")}
    data["workloads"] = {
        "containers": int(data.pop("containers", "0") or 0),
        "compose_projects": data.pop("compose_projects", "").split(),
        "swarm": data.pop("swarm", ""),
        "stacks": data.pop("stacks", "").split(),
        "services": [s for s in data.pop("swarm_services", "").split(",") if s],
        "deployments": [d for d in data.pop("deployments", "").split(",") if d],
        "pods": int(data.pop("pods", "0") or 0),
    }
    return data


def roles(servers: Servers) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {servers.gitea.host: ["gitea"]}
    for name, infra in servers.infrastructures.items():
        for i, node in enumerate(infra.nodes):
            part = ""
            if len(infra.nodes) > 1 and i == 0:
                part = " manager" if infra.type == "swarm" else " server" if infra.type == "k3s" else ""
            elif len(infra.nodes) > 1:
                part = " worker" if infra.type == "swarm" else " agent" if infra.type == "k3s" else ""
            out.setdefault(node, []).append(f"{name}: {infra.type}{part}")
    if servers.egress_proxy:
        out.setdefault(servers.egress_proxy.host, []).append("egress proxy")
    return out


def hosts_dir(platform: Platform) -> Path:
    return platform.state_dir / "hosts"


def record(platform: Platform, alias: str, data: dict) -> dict:
    folder = hosts_dir(platform)
    folder.mkdir(parents=True, exist_ok=True)
    data = {**data, "alias": alias, "seen": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (folder / f"{alias}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def refresh(
    platform: Platform,
    servers: Servers,
    aliases: list[str] | None = None,
    log=print,
    make_host: Callable[[str], Host] = Host,
) -> dict[str, dict]:
    wanted = aliases or list(roles(servers))
    out = {}
    for alias in wanted:
        data = record(platform, alias, inspect(make_host(alias)))
        out[alias] = data
        if data["reachable"]:
            inst = ", ".join(f"{k} {v}" for k, v in data["installed"].items() if v) or "nothing installed yet"
            log(f"  {alias:14} {data.get('pretty', '?'):24} {data.get('ip', '?'):15} {data['egress']:14} {inst}")
        else:
            log(f"  {alias:14} unreachable: {data['error']}")
    return out


def cached(platform: Platform) -> dict[str, dict]:
    folder = hosts_dir(platform)
    if not folder.exists():
        return {}
    out = {}
    for path in sorted(folder.glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text())
        except ValueError:
            continue
    return out


def drift(data: dict) -> list[str]:
    """Installed pieces whose version differs from the pinned one, as 'docker 29.9.1 (pinned 29.9.2)'."""
    out = []
    for name, have in (data.get("installed") or {}).items():
        want = expected_versions().get(name)
        if not want:
            continue
        if have and have != want and not (name == "k3s" and have.split("+")[0] == want.split("+")[0]):
            out.append(f"{name} {have} (pinned {want})")
    return out
