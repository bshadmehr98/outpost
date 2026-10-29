"""Keep the Gitea name in /etc/hosts across reboots.

    host.run(rewrite_script(entries))    # replace the lines for these names now
    keep_names(host, entries)            # and keep them at every boot; entries = [(ip, name), ...]

Nodes resolve the Gitea name through /etc/hosts, and /etc/hosts is not always ours to keep:
cloud-init rewrites it at every boot when `manage_etc_hosts` is on (the default on many cloud
images), and Docker regenerates it for a container (the lab). So besides writing the line now,
a oneshot unit puts it back at boot, after cloud-init and before CoreDNS, Docker and k3s.
When the egress proxy runs on a server other than Gitea, its name is kept the same way.

/etc/hosts can be a bind mount (in a container) that only allows writing in place, so both
scripts change the file in place instead of moving a new one over it.
"""

import shlex

from outpost.ssh import Host

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


def keep_names(host: Host, entries: list[tuple[str, str]]) -> None:
    """Install the boot script and its unit, and run the script once now.

    Args:
        entries: the (ip, name) lines to keep, Gitea's first.
    """
    ip, name = entries[0]
    host.write(SCRIPT_PATH, boot_script(ip, name, entries[1:]), mode="0755")
    host.write(UNIT_PATH, UNIT)
    host.run(
        f"systemctl daemon-reload && systemctl enable outpost-hosts.service >/dev/null 2>&1; {SCRIPT_PATH}",
        raise_on_failure=True,
    )


def boot_script(ip: str, name: str, extra: list[tuple[str, str]] | tuple = ()) -> str:
    """The text of /usr/local/sbin/outpost-hosts: add each line that is missing, at the end of the file.

    Args:
        extra: more (ip, name) pairs to keep, such as the egress proxy's.
    """
    add_line_if_missing = "".join(
        f"grep -q {shlex.quote(f' {n}$')} /etc/hosts || echo {shlex.quote(f'{i} {n}')} >> /etc/hosts\n"
        for i, n in [(ip, name), *extra]
    )
    return (
        "#!/bin/bash\n"
        "# Installed by Outpost. Makes sure the Gitea name resolves, whatever rewrote /etc/hosts.\n"
        + add_line_if_missing
    )


def rewrite_script(entries: list[tuple[str, str]]) -> str:
    """Shell that replaces the lines for these names now: drop the old ones, add the new ones."""
    drop_old_lines = " ".join(f"-e {shlex.quote(f' {n}$')}" for _, n in entries)
    print_new_lines = "; ".join(f"echo {shlex.quote(f'{i} {n}')}" for i, n in entries)
    return (
        f"{{ grep -v {drop_old_lines} /etc/hosts; {print_new_lines}; }} > /etc/hosts.outpost"
        " && cat /etc/hosts.outpost > /etc/hosts && rm /etc/hosts.outpost"
    )
