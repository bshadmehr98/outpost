"""inspect_server(): one script that says what a server runs, read back into a dict of facts.

The script prints one `key=value` line per fact. Every os-release variable is read with `${X:-}`:
Host.run turns on `set -u`, and os-release may leave out any of them (PRETTY_NAME, ID_LIKE, ...).
"""

import base64

from outpost.ssh import Host, SshError

FACTS_SCRIPT = r"""
. /etc/os-release 2>/dev/null || true
echo "id=${ID:-}"; echo "id_like=${ID_LIKE:-}"; echo "version_id=${VERSION_ID:-}"; echo "codename=${VERSION_CODENAME:-}"; echo "pretty=${PRETTY_NAME:-}"
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
ERROR_LENGTH = 300  # characters of an ssh error kept in the facts


def inspect_server(host: Host) -> dict:
    """What one server runs now. Never raises: an unreachable server comes back as
    {"reachable": False, "error": why}."""
    try:
        result = host.run(FACTS_SCRIPT, timeout=90)
    except SshError as e:
        return {"reachable": False, "error": _last_line(str(e))}
    if not result.stdout.strip():
        return {"reachable": False, "error": _last_line(result.stderr) or "no answer"}
    facts: dict = {"reachable": True, **_key_values(result.stdout)}
    facts["installed"] = {name: facts.pop(name, "") for name in INSTALLED}
    if "proxy_log_tail" in facts:
        facts["proxy_log_tail"] = _decode_log_tail(facts["proxy_log_tail"])
    facts["services"] = {key[len("svc_") :]: facts.pop(key) for key in list(facts) if key.startswith("svc_")}
    facts["workloads"] = _workloads(facts)
    return facts


def _key_values(output: str) -> dict[str, str]:
    """The `key=value` lines of the script's output; other lines are ignored."""
    values = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _decode_log_tail(encoded: str) -> list[str]:
    """The tinyproxy log lines. They are base64 on the wire, so a log line cannot break the key=value format."""
    try:
        return base64.b64decode(encoded).decode("utf-8", "replace").splitlines()
    except ValueError:
        return []


def _workloads(facts: dict) -> dict:
    """Take the workload lines out of facts, as numbers and lists."""
    return {
        "containers": int(facts.pop("containers", "0") or 0),
        "compose_projects": facts.pop("compose_projects", "").split(),
        "swarm": facts.pop("swarm", ""),
        "stacks": facts.pop("stacks", "").split(),
        "services": [s for s in facts.pop("swarm_services", "").split(",") if s],
        "deployments": [d for d in facts.pop("deployments", "").split(",") if d],
        "pods": int(facts.pop("pods", "0") or 0),
    }


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1][:ERROR_LENGTH] if lines else ""
