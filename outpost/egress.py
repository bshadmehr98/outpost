"""Seal a server: block outbound internet, keep Gitea, private networks and DNS reachable.

    install_seal(host, gitea_ip, allow)        # the script and its boot unit; the seal is not on yet
    seal_host(host, "why")                     # "sealed"
    open_host(host, "why"), seal_status(host)  # "open", "sealed" or "not installed"
    for host in target_hosts(system, "dev"): ...   # what `outpost egress <target>` acts on

What lands on the server:
    /usr/local/sbin/outpost-egress                 the script: seal|open|status [reason] (seal_script)
    /etc/systemd/system/outpost-egress.service     seals again at every boot, before Docker and k3s
    /var/log/outpost-egress.log                    one line per seal and open, with who and why

One chain, OUTPOST, built twice. In the `filter` table it ends in REJECT and is hooked into OUTPUT
(the server itself), FORWARD (everything the server routes: Docker containers, k3s pods) and DOCKER-USER,
so a connection fails instantly and a build that tries to reach PyPI tells you within a second.
In the `security` table, which the kernel consults after `filter`, it ends in DROP and is hooked into
OUTPUT and FORWARD again. That copy is the backstop: Docker, kube-proxy, kube-router and flannel all
insert ACCEPT rules at the top of `filter` FORWARD when they start, and whichever of them wins that
race, nothing they accept gets past the `security` table, which none of them touch.

The one exception Outpost ever makes is on the proxy host (infra/proxy), and only there: OUTPUT then
goes through a second chain, OUTPOST-OUT, in which packets the proxy's own user sends to TCP 443 and 80
RETURN before OUTPOST (with its final REJECT or DROP) is consulted, in the filter and the security copy
alike. The exception needs its own chain because the owner match only works for locally generated
packets: the kernel accepts `-m owner` only in chains reachable from OUTPUT and POSTROUTING (xt_owner's
hook mask) and refuses the rule with EINVAL in a chain that FORWARD or DOCKER-USER jump to, as OUTPOST
is. Forwarded packets (containers, pods) have no local owner anyway. Every other server gets exactly the
script it always had: no OUTPOST-OUT, no owner rule.

Sealed servers in the field run the script this module wrote. Keep its text stable: a verification
greps it ("egress script is the current one").
"""

import shlex

from outpost.client_config import PRIVATE_NETS
from outpost.ssh import Host
from outpost.system import AirGappedSystem

SCRIPT_PATH = "/usr/local/sbin/outpost-egress"
UNIT_PATH = "/etc/systemd/system/outpost-egress.service"
LOG_PATH = "/var/log/outpost-egress.log"
OUT_CHAIN = "OUTPOST-OUT"
PROXY_USER = "outpost-proxy"  # the system user the egress proxy runs as; the only owner the seal ever exempts


# -- install, seal, open ---------------------------------------------------------------


def install_seal(host: Host, gitea_ip: str, allow: list[str], proxy_user: str | None = None) -> None:
    """Write the seal script and its boot unit. The seal is not turned on: call seal_host for that.

    Args:
        gitea_ip: Gitea's address, always reachable.
        allow: more addresses or networks to reach (the private networks are always in).
        proxy_user: only on the proxy host: this user may then open TCP 443 and 80. On every other
            server, an exception left from an earlier proxy setup is removed.
    """
    host.write(SCRIPT_PATH, seal_script(gitea_ip, allow, proxy_user), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run(ENABLE_AT_BOOT, raise_on_failure=True)
    if not proxy_user:
        host.run(REMOVE_PROXY_EXCEPTION_IF_ANY, raise_on_failure=True)


def seal_host(host: Host, reason: str = "") -> str:
    """Turn the seal on. Returns what the script printed: "sealed".

    Args:
        reason: written to the server's egress log; any text (the dashboard asks the user for it).
    """
    return host.run(_seal_command("seal", reason), raise_on_failure=True).stdout.strip()


def open_host(host: Host, reason: str = "") -> str:
    """Turn the seal off. Returns what the script printed: "open".

    Args:
        reason: written to the server's egress log; any text (the dashboard asks the user for it).
    """
    return host.run(_seal_command("open", reason), raise_on_failure=True).stdout.strip()


def seal_status(host: Host) -> str:
    """ "sealed", "open", or "not installed"."""
    result = host.run(f"test -x {SCRIPT_PATH} && {SCRIPT_PATH} status || echo 'not installed'")
    return (result.stdout or result.stderr).strip()


def _seal_command(action: str, reason: str) -> str:
    """`outpost-egress seal|open [reason]`. The reason is one quoted argument: a ' in it broke the
    command, and $(...) in it ran on the server. The script logs its arguments joined by spaces, so
    the log line reads as before."""
    return " ".join([SCRIPT_PATH, action, *([shlex.quote(reason)] if reason else [])])


def target_hosts(system: AirGappedSystem, target: str) -> list[Host]:
    """The servers `outpost egress <target>` acts on: `gitea`, the nodes of an infrastructure, or one ssh alias."""
    if target == "gitea":
        return [system.gitea_host]
    if target in system.servers.infrastructures:
        return system.nodes(target)
    return [system.host(target)]


# -- the script ------------------------------------------------------------------------


def seal_script(gitea_ip: str, allow: list[str], proxy_user: str | None = None) -> str:
    """The text of /usr/local/sbin/outpost-egress.

    Args:
        proxy_user: only on the proxy host. Without it, the script is exactly the one every server had
            before the egress proxy existed.
    """
    allowed = " ".join(list(PRIVATE_NETS) + [a for a in allow if a not in PRIVATE_NETS])
    parts = _proxy_host_parts(proxy_user) if proxy_user else PLAIN_PARTS
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
LOG={LOG_PATH}
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


# The pieces of the script on every server but the proxy host.
PLAIN_PARTS = {
    "comment": "",
    "vars": "",
    "functions": "",
    # hook: put the jump to OUTPOST first in a chain, once; unhook: remove every jump to it
    "hook": 'hook() { $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null || $1 -t $2 -I "$3" 1 -j $CHAIN; }\n'
    'unhook() { while $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null; do $1 -t $2 -D "$3" -j $CHAIN; done; }',
    "seal": """      $ipt -N DOCKER-USER 2>/dev/null || true
      hook $ipt filter OUTPUT; hook $ipt filter FORWARD; hook $ipt filter DOCKER-USER
      hook $ipt security OUTPUT; hook $ipt security FORWARD""",
    "open": "",
    "status": "if iptables -C OUTPUT -j $CHAIN 2>/dev/null",
}


def _proxy_host_parts(proxy_user: str) -> dict[str, str]:
    """The pieces of the script on the proxy host: OUTPUT goes through OUTPOST-OUT, where the proxy's
    user may open TCP 443 and 80, then OUTPOST."""
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
        # the same as PLAIN_PARTS, with the chain to jump to as an optional 4th argument
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


# -- the boot unit, and the commands install_seal runs ---------------------------------

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

ENABLE_AT_BOOT = (
    f"systemctl daemon-reload && systemctl enable outpost-egress.service >/dev/null 2>&1 && touch {LOG_PATH}"
)

# When a server stops being the proxy host, its new script (without the exception) hooks OUTPUT straight
# to OUTPOST but knows nothing of OUTPOST-OUT. This removes the old hook and chain, sealing OUTPUT directly
# first so there is no moment without a seal. On a server that never ran the proxy it changes nothing.
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

# `iptables -S CHAIN` fails when the chain does not exist
REMOVE_PROXY_EXCEPTION_IF_ANY = (
    f"if iptables -S {OUT_CHAIN} >/dev/null 2>&1 || iptables -t security -S {OUT_CHAIN} >/dev/null 2>&1; then\n"
    f"{DROP_OUT_CHAIN}fi"
)
