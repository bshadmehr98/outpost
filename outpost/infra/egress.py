"""Seal a server: block outbound internet, keep Gitea, private networks and DNS reachable.

One chain, OUTPOST, built twice. In the `filter` table it ends in REJECT and is hooked into OUTPUT
(the host itself), FORWARD (everything the host routes: Docker containers, k3s pods) and DOCKER-USER,
so a connection fails instantly and a build that tries to reach PyPI tells you within a second.
In the `security` table, which the kernel consults after `filter`, it ends in DROP and is hooked into
OUTPUT and FORWARD again. That copy is the backstop: Docker, kube-proxy, kube-router and flannel all
insert ACCEPT rules at the top of `filter` FORWARD when they start, and whichever of them wins that
race, nothing they accept gets past the `security` table, which none of them touch.
"""

SCRIPT_PATH = "/usr/local/sbin/outpost-egress"
UNIT_PATH = "/etc/systemd/system/outpost-egress.service"
PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]


def script(gitea_ip: str, allow: list[str]) -> str:
    allowed = " ".join(PRIVATE + [a for a in allow if a not in PRIVATE])
    return f'''#!/bin/bash
# outpost-egress seal|open|status [reason]
# Installed by Outpost. "seal" blocks outbound internet for this host and its containers.
# Allowed: loopback, replies to established connections, DNS, the Gitea server, private networks
# and the extra addresses below. Everything else is rejected immediately. Toggles are logged.
# The chain lives in the filter table (REJECT, fast failure) and again in the security table
# (DROP), which the kernel checks last, so a rule Docker or k3s puts ahead of ours cannot let
# traffic out.
set -e
GITEA_IP="{gitea_ip}"
ALLOW="{allowed}"
LOG=/var/log/outpost-egress.log
CHAIN=OUTPOST
TOOLS=iptables
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

hook() {{ $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null || $1 -t $2 -I "$3" 1 -j $CHAIN; }}
unhook() {{ while $1 -t $2 -C "$3" -j $CHAIN 2>/dev/null; do $1 -t $2 -D "$3" -j $CHAIN; done; }}

case "${{1:-status}}" in
  seal)
    for ipt in $TOOLS; do
      build_chain $ipt filter REJECT
      build_chain $ipt security DROP
      $ipt -N DOCKER-USER 2>/dev/null || true
      hook $ipt filter OUTPUT; hook $ipt filter FORWARD; hook $ipt filter DOCKER-USER
      hook $ipt security OUTPUT; hook $ipt security FORWARD
    done
    log "seal ${{*:2}}"
    echo "sealed"
    ;;
  open)
    for ipt in $TOOLS; do
      unhook $ipt filter OUTPUT; unhook $ipt filter FORWARD; unhook $ipt filter DOCKER-USER
      unhook $ipt security OUTPUT; unhook $ipt security FORWARD
    done
    log "open ${{*:2}}"
    echo "open"
    ;;
  status)
    if iptables -C OUTPUT -j $CHAIN 2>/dev/null; then echo "sealed"; else echo "open"; fi
    ;;
  *)
    echo "usage: outpost-egress seal|open|status [reason]" >&2
    exit 1
    ;;
esac
'''


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


def install(host, gitea_ip: str, allow: list[str]) -> None:
    host.write(SCRIPT_PATH, script(gitea_ip, allow), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run("systemctl daemon-reload && systemctl enable outpost-egress.service >/dev/null 2>&1 && touch /var/log/outpost-egress.log")


def seal(host, reason: str = "") -> str:
    return host.run(f"{SCRIPT_PATH} seal {reason}").stdout.strip()


def open_(host, reason: str = "") -> str:
    return host.run(f"{SCRIPT_PATH} open {reason}").stdout.strip()


def status(host) -> str:
    result = host.run(f"test -x {SCRIPT_PATH} && {SCRIPT_PATH} status || echo 'not installed'", check=False)
    return (result.stdout or result.stderr).strip()
