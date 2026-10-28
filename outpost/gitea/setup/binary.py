"""Step 3 of `gitea setup`: the Gitea binary from the box, and git, which Gitea needs."""

from pathlib import Path

from outpost import versions
from outpost.gitea.backup import backup
from outpost.gitea.service import installed_version
from outpost.system import AirGappedSystem

GITEA_BINARY = "/usr/local/bin/gitea"
INSTALL_GIT = "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git 2>&1 | tail -2 || dnf install -y git"


def check_box_has_gitea(system: AirGappedSystem) -> Path:
    """The Gitea binary of the pinned version in the box.

    Raises:
        RuntimeError: it is not there.
    """
    binary = system.workspace.box_dir / "files" / "gitea" / versions.GITEA / "gitea"
    if not binary.exists():
        raise RuntimeError(f"{binary} is missing. Run `outpost fetch --only files` first.")
    return binary


def install_binary(system: AirGappedSystem, host, binary: Path, log=print) -> None:
    """Put the binary on the server, unless that version runs there already.

    An older Gitea is backed up into backups/ first: Gitea migrates its database on the next start.
    """
    installed = installed_version(host)
    if installed == versions.GITEA:
        return
    if installed:
        log(f"[{host.alias}] gitea {installed} is installed; backing it up before upgrading to {versions.GITEA}")
        backup(system, log=log)
    log(f"[{host.alias}] uploading gitea {versions.GITEA} binary ({binary.stat().st_size // 1_000_000} MB)")
    host.run("systemctl stop gitea 2>/dev/null || true", raise_on_failure=True)
    host.put(binary, GITEA_BINARY, mode="0755")


def ensure_git(host, log=print) -> None:
    """Install git with the server's package manager, if it is missing.

    Raises:
        RuntimeError: git is missing and could not be installed.
    """
    if host.run("command -v git").ok:
        return
    log(f"[{host.alias}] git is not installed; trying the host's package manager")
    if not host.run(INSTALL_GIT).ok or not host.run("command -v git").ok:
        raise RuntimeError(
            f"[{host.alias}] Gitea needs git. Install it once (apt-get install git), then rerun `outpost gitea setup`."
        )
