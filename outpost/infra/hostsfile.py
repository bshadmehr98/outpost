"""Keep the Gitea name in /etc/hosts across reboots.

Nodes resolve the Gitea name through /etc/hosts, and /etc/hosts is not always ours to keep:
cloud-init rewrites it at every boot when `manage_etc_hosts` is on (the default on many cloud
images), and Docker regenerates it for a container (the lab). So besides writing the line now,
a oneshot unit puts it back at boot, after cloud-init and before CoreDNS, Docker and k3s.
"""

import shlex

SCRIPT_PATH = "/usr/local/sbin/outpost-hosts"
UNIT_PATH = "/etc/systemd/system/outpost-hosts.service"

UNIT = f"""[Unit]
Description=Outpost: keep the Gitea name in /etc/hosts
After=network.target cloud-init.service cloud-final.service
Before=outpost-dns.service docker.service k3s.service k3s-agent.service gitea.service

[Service]
Type=oneshot
ExecStart={SCRIPT_PATH}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def script(ip: str, name: str) -> str:
    # append rather than rewrite: /etc/hosts can be a bind mount that only allows writing in place
    return (
        "#!/bin/bash\n"
        "# Installed by Outpost. Makes sure the Gitea name resolves, whatever rewrote /etc/hosts.\n"
        f"grep -q {shlex.quote(f' {name}$')} /etc/hosts || echo {shlex.quote(f'{ip} {name}')} >> /etc/hosts\n"
    )


def install(host, ip: str, name: str) -> None:
    host.write(SCRIPT_PATH, script(ip, name), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run(f"systemctl daemon-reload && systemctl enable outpost-hosts.service >/dev/null 2>&1; {SCRIPT_PATH}")
