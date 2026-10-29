"""What to show when the egress proxy cannot be installed or does not start.

Both scripts only read, and keep going when a probe fails: Host.run starts with `set -euo pipefail`,
so each begins with `set +e +o pipefail`.
"""

import shlex

from outpost.client_config import PATH_APT_LIST, PATH_CA, PATH_RPM_REPO
from outpost.endpoints import GiteaEndpoint
from outpost.infra.proxy.tinyproxy import ALLOW_PATH, CONF_PATH, LOG_PATH, USER
from outpost.needs import PROXY_PACKAGE
from outpost.ssh import Host

KEEP_GOING = "set +e +o pipefail\n"  # one failing probe must not end the report


def diagnose_script(family: str, gitea: GiteaEndpoint, distro: str) -> str:
    """What the package manager saw when it found no tinyproxy: the refresh in full, the package source
    Outpost wrote, what the index says about tinyproxy, and whether Gitea answers for the distro at all."""
    if family == "rpm":
        url = f"{gitea.rpm_repo}/{distro}/repodata/repomd.xml"
        return (
            KEEP_GOING + "echo '--- dnf makecache'; dnf makecache 2>&1 | tail -20\n"
            f"echo '--- {PATH_RPM_REPO}'; cat {PATH_RPM_REPO} 2>&1\n"
            "echo '--- dnf repolist'; dnf repolist -v 2>&1 | tail -20\n"
            f"echo '--- dnf info {PROXY_PACKAGE}'; dnf info {PROXY_PACKAGE} 2>&1 | tail -10\n"
            f"echo '--- {url}'; {_http_status(url)}"
        )
    url = f"{gitea.apt_repo}/dists/{distro}/Release"
    return (
        KEEP_GOING + "echo '--- apt-get update'; apt-get update 2>&1 | tail -20\n"
        f"echo '--- {PATH_APT_LIST}'; cat {PATH_APT_LIST} 2>&1\n"
        "echo '--- other sources'; ls /etc/apt/sources.list /etc/apt/sources.list.d/ 2>&1\n"
        f"echo '--- apt-cache policy {PROXY_PACKAGE}'; apt-cache policy {PROXY_PACKAGE} 2>&1\n"
        f"echo '--- {url}'; {_http_status(url)}"
        f"echo '--- getent hosts {gitea.name}'; getent hosts {shlex.quote(gitea.name)} 2>&1\n"
    )


def access_script(binary: str | None = None) -> str:
    """Why tinyproxy may not get at its files: every folder on the way with its mode, what the proxy user
    can read, and, with `binary`, tinyproxy run by hand as that user outside systemd's sandbox for two seconds."""
    lines = [
        KEEP_GOING.strip(),
        *[f"echo '--- namei -l {p}'; namei -l {p} 2>&1" for p in (CONF_PATH, ALLOW_PATH, LOG_PATH)],
        f"echo '--- id {USER}'; id {USER} 2>&1",
        f"echo '--- as {USER}'; runuser -u {USER} -- head -c 80 {CONF_PATH} >/dev/null 2>/tmp/outpost-proxy-read.err && echo readable || cat /tmp/outpost-proxy-read.err",
    ]
    if binary:
        lines += _run_by_hand(binary)
    return "\n".join(lines) + "\n"


def access_report(host: Host, binary: str | None = None) -> str:
    """The output of access_script on host, its last 2000 characters."""
    out = host.run(access_script(binary), timeout=60)
    return (out.stdout + out.stderr).strip()[-2000:]


def _http_status(url: str) -> str:
    """A line that prints the HTTP status Gitea answers url with."""
    return f"curl --cacert {PATH_CA} -sS -o /dev/null -w '%{{http_code}}\\n' --max-time 10 {shlex.quote(url)} 2>&1\n"


def _run_by_hand(binary: str) -> list[str]:
    """tinyproxy started by hand, as USER and as root, from its file and from a copy in /tmp. When the
    file is readable by the user but tinyproxy cannot open it, this tells a path-based MAC (AppArmor on
    the kernel, which a privileged container shares) from anything else."""
    last_lines_and_exit_code = '2>&1 | tail -5; echo "exit ${PIPESTATUS[0]}"'
    return [
        f"echo '--- {binary} -d -c {CONF_PATH} as {USER}, outside systemd'; "
        f"timeout 2 runuser -u {USER} -- {binary} -d -c {CONF_PATH} {last_lines_and_exit_code}",
        f"echo '--- as root'; timeout 2 {binary} -d -c {CONF_PATH} 2>&1 | tail -2; echo \"exit ${{PIPESTATUS[0]}}\"",
        f"cp {CONF_PATH} /tmp/outpost-proxy-copy.conf && chmod 0644 /tmp/outpost-proxy-copy.conf; "
        f"echo '--- copy in /tmp, as {USER}'; timeout 2 runuser -u {USER} -- {binary} -d -c /tmp/outpost-proxy-copy.conf 2>&1 | tail -2; echo \"exit ${{PIPESTATUS[0]}}\"",
        f"echo '--- ls -l {binary}'; ls -lL {binary} 2>&1; getcap {binary} 2>&1",
        "echo '--- apparmor'; cat /sys/module/apparmor/parameters/enabled 2>&1; "
        "grep -i tinyproxy /sys/kernel/security/apparmor/profiles 2>&1 | head -5; cat /proc/self/attr/current 2>&1",
        "echo '--- kernel denials'; dmesg 2>&1 | grep -iE 'denied|apparmor' | tail -8",
        "echo '--- systemctl show outpost-proxy'; systemctl show outpost-proxy -p User -p ProtectSystem -p ProtectHome -p PrivateTmp 2>&1",
    ]
