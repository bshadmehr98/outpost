"""`outpost infra upgrade`: an OS upgrade on the nodes, answered by Gitea only.

apt-get on Debian and Ubuntu, apk on Alpine, dnf on the RHEL family, decided on the node from
/etc/os-release. Put the packages to keep current in a needs file (needs/os-updates.yml), fetch,
upload, then run this; the seal stays on throughout.
"""

from outpost import hosts
from outpost.ssh import Host
from outpost.system import AirGappedSystem

UPGRADE_SCRIPT = """. /etc/os-release 2>/dev/null
case " $ID $ID_LIKE " in
  *alpine*)
    apk update 2>&1 | tail -3
    apk upgrade 2>&1 | tail -25
    ;;
  *rhel*|*fedora*|*centos*|*rocky*|*almalinux*)
    dnf makecache -q 2>&1 | tail -3
    dnf upgrade -y -q 2>&1 | tail -25
    if command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; then echo 'REBOOT-REQUIRED'; fi
    ;;
  *)
    apt-get update -qq 2>&1 | tail -3
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q -o Dpkg::Options::=--force-confold 2>&1 | grep -E '^[0-9]+ upgraded|^Setting up|^E:|^W:|Unpacking' | tail -25
    test -f /var/run/reboot-required && echo 'REBOOT-REQUIRED' || true
    ;;
esac
"""
ERROR_MARKERS = ("E:", "ERROR:", "Error:")  # how apt, apk and dnf start an error line
REBOOT_REQUIRED = "REBOOT-REQUIRED"


def upgrade_nodes(system: AirGappedSystem, nodes: list[Host], log=print) -> bool:
    """Upgrade the OS packages of each node from Gitea, then record what it runs now.

    Args:
        nodes: the Hosts to upgrade, usually system.nodes(name).

    Returns:
        True when every node upgraded; the log says which failed.
    """
    failed = [node.alias for node in nodes if not _upgrade_node(system, node, log)]
    if failed:
        log(f"upgrade failed on {', '.join(failed)}")
        return False
    log("done; the nodes stayed sealed throughout")
    return True


def _upgrade_node(system: AirGappedSystem, node: Host, log) -> bool:
    log(f"==> {node.alias}: package index update and upgrade from Gitea")
    result = node.run(UPGRADE_SCRIPT, timeout=3600)
    for line in result.stdout.strip().splitlines():
        log(f"  [{node.alias}] {line}")
    upgraded = result.ok and not any(marker in result.stdout for marker in ERROR_MARKERS)
    if not upgraded:
        log(f"  [{node.alias}] FAILED: {result.stderr.strip()[-300:]}")
    elif REBOOT_REQUIRED in result.stdout:
        log(f"  [{node.alias}] a reboot is required for the new kernel or libc to take effect")
    hosts.record(system.workspace, node.alias, hosts.inspect(node))
    return upgraded
