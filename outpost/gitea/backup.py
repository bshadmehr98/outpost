"""Back up and restore the Gitea server: its SQLite database, repositories, packages and config.

    backup(system, out_dir=None, keep_on_server=False, log=print)   -> backups/gitea-<name>-<stamp>.tar.gz
    restore(system, archive, log=print)

  outpost gitea backup [--out DIR]       stop Gitea for the few seconds a tar takes, archive
                                         /var/lib/gitea/data and /etc/gitea, start it again,
                                         copy the archive to the laptop, remove it from the server
  outpost gitea restore <archive>        copy the archive up, stop Gitea, move the current data
                                         aside, extract, start, check the stored token still works

The archive is plain tar+gzip of two folders, so it can also be opened by hand. Gitea is stopped
during the archive step because SQLite must not be copied while it is written to. On a server
with a few gigabytes of packages the pause is under a minute. `gitea setup` takes a backup by
itself before it upgrades Gitea to a new version.
"""

import time
from pathlib import Path

from outpost.gitea.service import CONF_DIR, DATA_DIR, installed_version, token_works, wait_for_gitea
from outpost.http import sha256_file
from outpost.system import AirGappedSystem
from outpost.workspace import GITEA_TOKEN, STATE_BACKUP, STATE_GITEA

REMOTE_DIR = "/var/tmp/outpost-backups"  # on disk, readable by the ssh user, cleaned after the copy
RESTORE_ARCHIVE = f"{REMOTE_DIR}/restore.tar.gz"
ARCHIVE_TIMEOUT = 7200  # seconds for a tar of a big Gitea


def backup(system: AirGappedSystem, out_dir: Path | None = None, keep_on_server: bool = False, log=print) -> Path:
    """Archive the Gitea server's data and config, and copy the archive to the laptop.

    Args:
        out_dir: where the archive goes (default: backups/ in the workspace).
        keep_on_server: also leave the archive in /var/tmp/outpost-backups on the server.

    Returns:
        The archive on the laptop.

    Raises:
        RuntimeError: Gitea is not installed on the server.
    """
    gitea, host = system.servers.gitea, system.gitea_host
    version = installed_version(host)
    if not version:
        raise RuntimeError(f"[{gitea.host}] Gitea is not installed there; nothing to back up.")
    name = f"gitea-{gitea.name}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    remote = f"{REMOTE_DIR}/{name}"
    log(f"[{gitea.host}] pausing Gitea {version} and archiving {DATA_DIR} and {CONF_DIR}")
    started = time.time()
    _archive_on_server(host, remote)
    wait_for_gitea(host)
    size = int(host.run(f"stat -c %s {remote}", raise_on_failure=True).stdout.strip() or 0)
    log(f"[{gitea.host}] Gitea is back; archive is {size / 1e6:.0f} MB, took {int(time.time() - started)}s")
    out_dir = out_dir or system.workspace.backups_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / name
    log(f"copying to {local}")
    host.get(remote, local)
    if not keep_on_server:
        host.run(f"rm -f {remote}", raise_on_failure=True)
    system.write_state(
        STATE_BACKUP,
        {
            "file": str(local),
            "bytes": local.stat().st_size,
            "sha256": sha256_file(local),
            "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "gitea": version,
            "host": gitea.host,
        },
    )
    log(f"backup done: {local} ({local.stat().st_size / 1e6:.0f} MB). Keep it somewhere that is not this laptop.")
    return local


def restore(system: AirGappedSystem, archive: Path, log=print) -> None:
    """Put a backup back on the Gitea server. The data it replaces is kept beside it, renamed.

    Raises:
        RuntimeError: the archive does not exist, or Gitea is not installed on the server.
    """
    gitea, host = system.servers.gitea, system.gitea_host
    if not archive.exists():
        raise RuntimeError(f"{archive} does not exist")
    if not installed_version(host):
        raise RuntimeError(
            f"[{gitea.host}] Gitea is not installed there. Run `outpost gitea setup` first, then restore."
        )
    _copy_archive_up(host, archive, log)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log(f"[{gitea.host}] stopping Gitea, keeping the current data at {DATA_DIR}.before-restore-{stamp}")
    _swap_data(host, stamp)
    _put_back_laptop_certificate(system, host)
    wait_for_gitea(host)
    version = installed_version(host)
    log(f"[{gitea.host}] Gitea {version} is up with the restored data")
    _check_token(system, host, log)
    _record_restore(system, version, archive)
    log(f"done. When everything looks right, remove {DATA_DIR}.before-restore-{stamp} on the server.")


# -- backup steps -----------------------------------------------------------------------------


def _archive_on_server(host, remote: str) -> None:
    """Stop Gitea, tar its data and config into remote, and start it again, even when the tar fails."""
    host.run(
        f"mkdir -p {REMOTE_DIR} && chmod 755 {REMOTE_DIR}\n"
        "trap 'systemctl start gitea' EXIT\n"
        "systemctl stop gitea\n"
        f"tar -C / -cf - {DATA_DIR[1:]} {CONF_DIR[1:]} | gzip -1 > {remote}\n"
        f"chmod 644 {remote}\n",
        timeout=ARCHIVE_TIMEOUT,
        raise_on_failure=True,
    )


# -- restore steps ----------------------------------------------------------------------------


def _copy_archive_up(host, archive: Path, log) -> None:
    log(f"[{host.alias}] copying {archive.name} ({archive.stat().st_size / 1e6:.0f} MB) to the server")
    host.run(f"mkdir -p {REMOTE_DIR} && chmod 755 {REMOTE_DIR}", raise_on_failure=True)
    host.put(archive, RESTORE_ARCHIVE, via="/var/tmp")


def _swap_data(host, stamp: str) -> None:
    """Stop Gitea, move its data aside to <data>.before-restore-<stamp>, and extract the archive in its place."""
    host.run(
        "systemctl stop gitea\n"
        f"if [ -d {DATA_DIR} ]; then mv {DATA_DIR} {DATA_DIR}.before-restore-{stamp}; fi\n"
        f"tar -xzf {RESTORE_ARCHIVE} -C /\n"
        f"rm -f {RESTORE_ARCHIVE}\n"
        f"chown -R git:git /var/lib/gitea && chown git:git {CONF_DIR}/app.ini && chmod 640 {CONF_DIR}/app.ini\n",
        timeout=ARCHIVE_TIMEOUT,
        raise_on_failure=True,
    )


def _put_back_laptop_certificate(system: AirGappedSystem, host) -> None:
    """Write the laptop's Gitea certificate over the archive's, then start Gitea.

    The certificate on the laptop is the one every node trusts; an archive may carry an older one.
    """
    cert, key = system.workspace.gitea_cert, system.workspace.gitea_key
    if cert.exists() and key.exists():
        host.write(f"{CONF_DIR}/gitea.crt", cert.read_text())
        host.write(f"{CONF_DIR}/gitea.key", key.read_text(), mode="0640")
    host.run(f"chown root:git {CONF_DIR}/gitea.key {CONF_DIR}/gitea.crt; systemctl start gitea", raise_on_failure=True)


def _check_token(system: AirGappedSystem, host, log) -> None:
    """Say so when the restored Gitea does not know secrets/gitea-token."""
    token = system.secret(GITEA_TOKEN)
    if not token or not token_works(host, token):
        log(
            "the token in secrets/gitea-token does not belong to this data; run `outpost gitea setup` to make a new one"
        )


def _record_restore(system: AirGappedSystem, version: str, archive: Path) -> None:
    gitea = system.servers.gitea
    system.write_state(
        STATE_GITEA,
        {
            **(system.state(STATE_GITEA) or {}),
            "name": gitea.name,
            "host": gitea.host,
            "version": version,
            "restored_from": archive.name,
            "restored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
