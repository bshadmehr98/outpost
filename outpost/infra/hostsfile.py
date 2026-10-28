"""Keep the Gitea name in /etc/hosts across reboots.

Nodes resolve the Gitea name through /etc/hosts, and /etc/hosts is not always ours to keep:
cloud-init rewrites it at every boot when `manage_etc_hosts` is on (the default on many cloud
images), and Docker regenerates it for a container (the lab). So besides writing the line now,
a oneshot unit puts it back at boot, after cloud-init and before CoreDNS, Docker and k3s.
When the egress proxy runs on a host other than Gitea, its name is kept the same way (`extra`).
"""

import shlex

SCRIPT_PATH = "/usr/local/sbin/outpost-hosts"
UNIT_PATH = "/etc/systemd/system/outpost-hosts.service"

UNIT = f"""[Unit]
Description=Outpost: keep the Gitea name (and the egress proxy's) in /etc/hosts
After=network.target cloud-init.service cloud-final.service
Before=outpost-dns.service docker.service k3s.service k3s-agent.service gitea.service

[Service]
Type=oneshot
ExecStart={SCRIPT_PATH}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def script(ip: str, name: str, extra: list[tuple[str, str]] | tuple = ()) -> str:
    """`extra` holds more (ip, name) pairs to keep, such as the egress proxy's."""
    # append rather than rewrite: /etc/hosts can be a bind mount that only allows writing in place
    lines = "".join(
        f"grep -q {shlex.quote(f' {n}$')} /etc/hosts || echo {shlex.quote(f'{i} {n}')} >> /etc/hosts\n"
        for i, n in [(ip, name), *extra]
    )
    return (
        "#!/bin/bash\n"
        "# Installed by Outpost. Makes sure the Gitea name resolves, whatever rewrote /etc/hosts.\n" + lines
    )


def rewrite(entries: list[tuple[str, str]]) -> str:
    """Shell that replaces the lines for these names now, in place (a bind-mounted /etc/hosts cannot be moved)."""
    drop = " ".join(f"-e {shlex.quote(f' {n}$')}" for _, n in entries)
    add = "; ".join(f"echo {shlex.quote(f'{i} {n}')}" for i, n in entries)
    return f"{{ grep -v {drop} /etc/hosts; {add}; }} > /etc/hosts.outpost && cat /etc/hosts.outpost > /etc/hosts && rm /etc/hosts.outpost"


def install(host, ip: str, name: str, extra: list[tuple[str, str]] | tuple = ()) -> None:
    host.write(SCRIPT_PATH, script(ip, name, extra), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run(
        f"systemctl daemon-reload && systemctl enable outpost-hosts.service >/dev/null 2>&1; {SCRIPT_PATH}",
        raise_on_failure=True,
    )
