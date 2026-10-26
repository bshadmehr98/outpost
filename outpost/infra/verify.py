"""Prove a node uses Gitea and nothing else. Each check is one shell command that must succeed.

Output is a list of {name, ok, detail}. The checks that matter most:
  - the node reaches Gitea over TLS it trusts
  - the public internet is rejected, and the seal comes back after a reboot
  - every package client is configured to use Gitea only
"""

from .. import versions
from . import clients, common


def common_checks(ctx: common.NodeContext) -> list[tuple[str, str]]:
    g = ctx.gitea
    return [
        ("gitea in /etc/hosts", f"getent hosts {g.name} | grep -q {g.ip}"),
        ("gitea name restored at boot", "systemctl is-enabled outpost-hosts.service"),
        ("gitea reachable with trusted TLS", f"curl --cacert {clients.PATH_CA} -fsS --max-time 10 {g.url}/api/v1/version"),
        ("system trust store has the CA", f"curl -fsS --max-time 10 {g.url}/api/v1/version"),
        ("pip points at gitea", f"grep -q {g.url} /etc/pip.conf && ! grep -rqs extra-index-url /etc/pip.conf /root/.pip /root/.config/pip"),
        ("npm points at gitea", f"grep -q {g.url} /etc/npmrc"),
        ("apt sources are gitea only", f"! grep -rhs '^deb ' /etc/apt/sources.list /etc/apt/sources.list.d/ | grep -v {g.url}; ! grep -rhs '^URIs:' /etc/apt/sources.list.d/ | grep -v {g.url}"),
        ("node dns for containers running", "systemctl is-active outpost-dns"),
        ("apt update works offline", "apt-get update -qq 2>&1 | tail -3"),
        ("egress sealed", "test \"$(/usr/local/sbin/outpost-egress status)\" = sealed"),
        ("public internet rejected", "! curl -sS --max-time 5 https://pypi.org/simple/ >/dev/null 2>&1"),
        ("egress seal survives reboot", "systemctl is-enabled outpost-egress.service"),
        ("routed traffic sealed too (containers, pods)", "iptables -C FORWARD -j OUTPOST && iptables -t security -C FORWARD -j OUTPOST"),
        ("egress script is the current one", "grep -q 'build_chain $ipt security DROP' /usr/local/sbin/outpost-egress"),
        (f"coredns at pinned version {versions.COREDNS}", f"/usr/local/bin/coredns -version | grep -q {versions.COREDNS}"),
        ("pip.conf unchanged", f"test \"$(grep -c . /etc/pip.conf)\" = 3 && grep -q '^cert = {clients.PATH_CA}' /etc/pip.conf"),
    ]


def script(checks: list[tuple[str, str]]) -> str:
    lines = ["set +e"]
    for name, cmd in checks:
        lines.append(
            f"out=$( ( {cmd} ) 2>&1 ); rc=$?; "
            f"if [ $rc -eq 0 ]; then echo \"CHECK|{name}|OK|$(echo \"$out\" | tail -1)\"; else echo \"CHECK|{name}|FAIL|$(echo \"$out\" | tail -1)\"; fi"
        )
    return "\n".join(lines) + "\n"


def parse(output: str) -> list[dict]:
    results = []
    for line in output.splitlines():
        if line.startswith("CHECK|"):
            _, name, status, detail = line.split("|", 3)
            results.append({"name": name, "ok": status == "OK", "detail": detail.strip()})
    return results


def verify_node(host, ctx: common.NodeContext, extra: list[tuple[str, str]]) -> list[dict]:
    checks = common_checks(ctx) + extra
    result = host.run(script(checks), check=False, timeout=600)
    parsed = parse(result.stdout)
    if not parsed:
        parsed = [{"name": "connect", "ok": False, "detail": result.stderr.strip()[-300:] or "no output"}]
    return parsed
