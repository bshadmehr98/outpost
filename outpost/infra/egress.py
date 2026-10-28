"""Seal a server: block outbound internet, keep Gitea, private networks and DNS reachable.

One chain, OUTPOST, built twice. In the `filter` table it ends in REJECT and is hooked into OUTPUT
(the host itself), FORWARD (everything the host routes: Docker containers, k3s pods) and DOCKER-USER,
so a connection fails instantly and a build that tries to reach PyPI tells you within a second.
In the `security` table, which the kernel consults after `filter`, it ends in DROP and is hooked into
OUTPUT and FORWARD again. That copy is the backstop: Docker, kube-proxy, kube-router and flannel all
insert ACCEPT rules at the top of `filter` FORWARD when they start, and whichever of them wins that
race, nothing they accept gets past the `security` table, which none of them touch.

The one exception Outpost ever makes is on the host that runs the egress proxy (infra/proxy.py), and
only there: OUTPUT then goes through a second chain, OUTPOST-OUT, in which packets the proxy's own user
sends to TCP 443 and 80 RETURN before OUTPOST (with its final REJECT or DROP) is consulted, in the filter
and the security copy alike. The exception needs its own chain because the owner match only works for
locally generated packets: the kernel accepts `-m owner` only in chains reachable from OUTPUT and
POSTROUTING (xt_owner's hook mask) and refuses the rule with EINVAL in a chain that FORWARD or
DOCKER-USER jump to, as OUTPOST is. Forwarded packets (containers, pods) have no local owner anyway.
Every other host gets exactly the script it always had: no OUTPOST-OUT, no owner rule.
"""

from ..system import AirGappedSystem

SCRIPT_PATH = "/usr/local/sbin/outpost-egress"
UNIT_PATH = "/etc/systemd/system/outpost-egress.service"
PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
OUT_CHAIN = "OUTPOST-OUT"
PROXY_USER = "outpost-proxy"  # the system user the egress proxy runs as; the only owner the seal ever exempts


def _owner_parts(proxy_user: str) -> dict[str, str]:
    """The pieces of the script that differ on the proxy host."""
    return {
        "comment": (
            "\n# On this host OUTPUT goes through OUTPOST-OUT first: the egress proxy's own user may open\n"
            "# TCP 443 and 80 (the named exceptions, enforced by the proxy's allow list); then OUTPOST."
        ),
        "vars": f'PROXY_USER="{proxy_user}"\nOUT_CHAIN={OUT_CHAIN}\n',
        "functions": """
build_out_chain() {  # <iptables|ip6tables> <table>: hooked into OUTPUT only
  local ipt=$1 t=$2
  $ipt -t $t -N $OUT_CHAIN 2>/dev/null || true
  $ipt -t $t -F $OUT_CHAIN
  # Only for IPv4 and only when the user exists: a rule naming a missing user fails, and with `set -e`
  # a failed rule at boot would leave the host unsealed.
  if [ $ipt = iptables ] && id -u "$PROXY_USER" >/dev/null 2>&1; then
    $ipt -t $t -A $OUT_CHAIN -m owner --uid-owner "$PROXY_USER" -p tcp --dport 443 -j RETURN
    $ipt -t $t -A $OUT_CHAIN -m owner --uid-owner "$PROXY_USER" -p tcp --dport 80 -j RETURN
  fi
  $ipt -t $t -A $OUT_CHAIN -j $CHAIN
}
""",
        "hook": 'hook() { local c=${4:-$CHAIN}; $1 -t $2 -C "$3" -j $c 2>/dev/null || $1 -t $2 -I "$3" 1 -j $c; }\n'
        'unhook() { local c=${4:-$CHAIN}; while $1 -t $2 -C "$3" -j $c 2>/dev/null; do $1 -t $2 -D "$3" -j $c; done; }',
        "seal": """      build_out_chain $ipt filter
      build_out_chain $ipt security
      $ipt -N DOCKER-USER 2>/dev/null || true
      hook $ipt filter OUTPUT $OUT_CHAIN; hook $ipt filter FORWARD; hook $ipt filter DOCKER-USER
      hook $ipt security OUTPUT $OUT_CHAIN; hook $ipt security FORWARD
      # a direct OUTPUT -> OUTPOST hook (the script without the exception) would make it ineffective
      unhook $ipt filter OUTPUT; unhook $ipt security OUTPUT""",
        "open": "      unhook $ipt filter OUTPUT $OUT_CHAIN; unhook $ipt security OUTPUT $OUT_CHAIN\n",
        "status": "if iptables -C OUTPUT -j $OUT_CHAIN 2>/dev/null || iptables -C OUTPUT -j $CHAIN 2>/dev/null",
    }


PLAIN = {
    "comment": "",
    "vars": "",
    "functions": "",
    "hook": 'hook() { $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null || $1 -t $2 -I "$3" 1 -j $CHAIN; }\n'
    'unhook() { while $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null; do $1 -t $2 -D "$3" -j $CHAIN; done; }',
    "seal": """      $ipt -N DOCKER-USER 2>/dev/null || true
      hook $ipt filter OUTPUT; hook $ipt filter FORWARD; hook $ipt filter DOCKER-USER
      hook $ipt security OUTPUT; hook $ipt security FORWARD""",
    "open": "",
    "status": "if iptables -C OUTPUT -j $CHAIN 2>/dev/null",
}


def script(gitea_ip: str, allow: list[str], proxy_user: str | None = None) -> str:
    """The seal. `proxy_user` only on the host that runs the egress proxy; without it the script is
    exactly the one every host had before the proxy existed."""
    allowed = " ".join(PRIVATE + [a for a in allow if a not in PRIVATE])
    parts = _owner_parts(proxy_user) if proxy_user else PLAIN
    return f'''#!/bin/bash
# outpost-egress seal|open|status [reason]
# Installed by Outpost. "seal" blocks outbound internet for this host and its containers.
# Allowed: loopback, replies to established connections, DNS, the Gitea server, private networks
# and the extra addresses below. Everything else is rejected immediately. Toggles are logged.
# The chain lives in the filter table (REJECT, fast failure) and again in the security table
# (DROP), which the kernel checks last, so a rule Docker or k3s puts ahead of ours cannot let
# traffic out.{parts["comment"]}
set -e
GITEA_IP="{gitea_ip}"
ALLOW="{allowed}"
LOG=/var/log/outpost-egress.log
CHAIN=OUTPOST
{parts["vars"]}TOOLS=iptables
command -v ip6tables >/dev/null 2>&1 && TOOLS="iptables ip6tables"

log() {{ echo "$(date -Is) ${{SUDO_USER:-root}} $*" >> "$LOG"; }}

build_chain() {{  # <iptables|ip6tables> <table> <REJECT|DROP>
  local ipt=$1 t=$2 last=$3
  $ipt -t $t -N $CHAIN 2>/dev/null || true
  $ipt -t $t -F $CHAIN
  $ipt -t $t -A $CHAIN -o lo -j RETURN
  $ipt -t $t -A $CHAIN -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  if [ $ipt = iptables ]; then
    $ipt -t $t -A $CHAIN -p udp --dport 53 -j RETURN
    $ipt -t $t -A $CHAIN -p tcp --dport 53 -j RETURN
    $ipt -t $t -A $CHAIN -d "$GITEA_IP" -j RETURN
    for net in $ALLOW; do $ipt -t $t -A $CHAIN -d "$net" -j RETURN; done
    if [ $last = REJECT ]; then $ipt -t $t -A $CHAIN -j REJECT --reject-with icmp-admin-prohibited; else $ipt -t $t -A $CHAIN -j DROP; fi
  else
    $ipt -t $t -A $CHAIN -d fe80::/10 -j RETURN
    if [ $last = REJECT ]; then $ipt -t $t -A $CHAIN -j REJECT --reject-with icmp6-adm-prohibited; else $ipt -t $t -A $CHAIN -j DROP; fi
  fi
}}
{parts["functions"]}
{parts["hook"]}

case "${{1:-status}}" in
  seal)
    for ipt in $TOOLS; do
      build_chain $ipt filter REJECT
      build_chain $ipt security DROP
{parts["seal"]}
    done
    log "seal ${{*:2}}"
    echo "sealed"
    ;;
  open)
    for ipt in $TOOLS; do
{parts["open"]}      unhook $ipt filter OUTPUT; unhook $ipt filter FORWARD; unhook $ipt filter DOCKER-USER
      unhook $ipt security OUTPUT; unhook $ipt security FORWARD
    done
    log "open ${{*:2}}"
    echo "open"
    ;;
  status)
    {parts["status"]}; then echo "sealed"; else echo "open"; fi
    ;;
  *)
    echo "usage: outpost-egress seal|open|status [reason]" >&2
    exit 1
    ;;
esac
'''


# When a host stops being the proxy host, its new script (without the exception) hooks OUTPUT straight to
# OUTPOST but knows nothing of OUTPOST-OUT. This removes the old hook and chain, sealing OUTPUT directly
# first so there is no moment without a seal. On a host that never ran the proxy it changes nothing.
DROP_OUT_CHAIN = f"""for ipt in iptables ip6tables; do
  command -v $ipt >/dev/null 2>&1 || continue
  for t in filter security; do
    if $ipt -t $t -C OUTPUT -j {OUT_CHAIN} 2>/dev/null; then
      $ipt -t $t -C OUTPUT -j OUTPOST 2>/dev/null || $ipt -t $t -I OUTPUT 1 -j OUTPOST
      while $ipt -t $t -C OUTPUT -j {OUT_CHAIN} 2>/dev/null; do $ipt -t $t -D OUTPUT -j {OUT_CHAIN}; done
    fi
    $ipt -t $t -F {OUT_CHAIN} 2>/dev/null && $ipt -t $t -X {OUT_CHAIN} 2>/dev/null || true
  done
done
"""


UNIT = f"""[Unit]
Description=Outpost egress seal (block outbound internet at boot)
After=network.target
Before=docker.service k3s.service k3s-agent.service

[Service]
Type=oneshot
ExecStart={SCRIPT_PATH} seal boot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def install(host, gitea_ip: str, allow: list[str], proxy_user: str | None = None) -> None:
    """`proxy_user` only on the host that runs the egress proxy: its user may then open 443 and 80."""
    host.write(SCRIPT_PATH, script(gitea_ip, allow, proxy_user), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run(
        "systemctl daemon-reload && systemctl enable outpost-egress.service >/dev/null 2>&1 && touch /var/log/outpost-egress.log",
        raise_on_failure=True,
    )
    if not proxy_user:
        host.run(
            f"if iptables -S {OUT_CHAIN} >/dev/null 2>&1 || iptables -t security -S {OUT_CHAIN} >/dev/null 2>&1; then\n{DROP_OUT_CHAIN}fi",
            raise_on_failure=True,
        )


def seal(host, reason: str = "") -> str:
    return host.run(f"{SCRIPT_PATH} seal {reason}", raise_on_failure=True).stdout.strip()


def open_(host, reason: str = "") -> str:
    return host.run(f"{SCRIPT_PATH} open {reason}", raise_on_failure=True).stdout.strip()


def status(host) -> str:
    result = host.run(f"test -x {SCRIPT_PATH} && {SCRIPT_PATH} status || echo 'not installed'")
    return (result.stdout or result.stderr).strip()


def target_hosts(system: AirGappedSystem, target: str) -> list:
    """The servers `outpost egress <target>` acts on: `gitea`, the nodes of an infrastructure, or one ssh alias."""
    if target == "gitea":
        return [system.gitea_host]
    if target in system.servers.infrastructures:
        return system.nodes(target)
    return [system.host(target)]
