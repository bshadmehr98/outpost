"""Back up and restore the Gitea server: its SQLite database, repositories, packages and config.

  outpost gitea backup [--out backups]   stop Gitea for the few seconds a tar takes, archive
                                         /var/lib/gitea/data and /etc/gitea, start it again,
                                         copy the archive to the laptop, remove it from the server
  outpost gitea restore <archive>        copy the archive up, stop Gitea, move the current data
                                         aside, extract, start, check the stored token still works

The archive is plain tar+gzip of two folders, so it can also be opened by hand. Gitea is stopped
during the archive step because SQLite must not be copied while it is written to. On a server
with a few gigabytes of packages the pause is under a minute. `gitea setup` takes a backup by
itself before it upgrades Gitea to a new version.
"""

import hashlib
import time
from pathlib import Path

from ..config import Servers
from ..paths import Platform
from ..ssh import Host
from .service import CONF_DIR, DATA_DIR, installed_version, wait_for_gitea

REMOTE_DIR = "/var/tmp/outpost-backups"   # on disk, readable by the SSH user, cleaned after the copy


def backup(platform: Platform, servers: Servers, out_dir: Path, keep_on_server: bool = False, log=print, host=None) -> Path:
    cfg = servers.gitea
    host = host or Host(cfg.host)
    version = installed_version(host)
    if not version:
        raise RuntimeError(f"[{cfg.host}] Gitea is not installed there; nothing to back up.")
    name = f"gitea-{cfg.name}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    remote = f"{REMOTE_DIR}/{name}"
    log(f"[{cfg.host}] pausing Gitea {version} and archiving {DATA_DIR} and {CONF_DIR}")
    started = time.time()
    host.run(
        f"mkdir -p {REMOTE_DIR} && chmod 755 {REMOTE_DIR}\n"
        "trap 'systemctl start gitea' EXIT\n"
        "systemctl stop gitea\n"
        f"tar -C / -cf - {DATA_DIR[1:]} {CONF_DIR[1:]} | gzip -1 > {remote}\n"
        f"chmod 644 {remote}\n", timeout=7200, raise_on_failure=True)
    wait_for_gitea(host)
    size = int(host.run(f"stat -c %s {remote}", raise_on_failure=True).stdout.strip() or 0)
    log(f"[{cfg.host}] Gitea is back; archive is {size / 1e6:.0f} MB, took {int(time.time() - started)}s")
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / name
    log(f"copying to {local}")
    host.get(remote, local)
    if not keep_on_server:
        host.run(f"rm -f {remote}", raise_on_failure=True)
    digest = hashlib.sha256(local.read_bytes()).hexdigest() if size < 2_000_000_000 else ""
    platform.write_state("backup", {"file": str(local), "bytes": local.stat().st_size, "sha256": digest,
                                    "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "gitea": version, "host": cfg.host})
    log(f"backup done: {local} ({local.stat().st_size / 1e6:.0f} MB). Keep it somewhere that is not this laptop.")
    return local


def restore(platform: Platform, servers: Servers, archive: Path, log=print, host=None) -> None:
    cfg = servers.gitea
    host = host or Host(cfg.host)
    if not archive.exists():
        raise RuntimeError(f"{archive} does not exist")
    if not installed_version(host):
        raise RuntimeError(f"[{cfg.host}] Gitea is not installed there. Run `outpost gitea setup` first, then restore.")
    remote = f"{REMOTE_DIR}/restore.tar.gz"
    log(f"[{cfg.host}] copying {archive.name} ({archive.stat().st_size / 1e6:.0f} MB) to the server")
    host.run(f"mkdir -p {REMOTE_DIR} && chmod 755 {REMOTE_DIR}", raise_on_failure=True)
    host.put(archive, remote, via="/var/tmp")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log(f"[{cfg.host}] stopping Gitea, keeping the current data at {DATA_DIR}.before-restore-{stamp}")
    host.run(
        "systemctl stop gitea\n"
        f"if [ -d {DATA_DIR} ]; then mv {DATA_DIR} {DATA_DIR}.before-restore-{stamp}; fi\n"
        f"tar -xzf {remote} -C /\n"
        f"rm -f {remote}\n"
        f"chown -R git:git /var/lib/gitea && chown git:git {CONF_DIR}/app.ini && chmod 640 {CONF_DIR}/app.ini\n", timeout=7200, raise_on_failure=True)
    # the certificate on the laptop is the one every node trusts; an archive may carry an older one
    cert, key = platform.pki_dir / "gitea.crt", platform.secrets_dir / "gitea.key"
    if cert.exists() and key.exists():
        host.write(f"{CONF_DIR}/gitea.crt", cert.read_text())
        host.write(f"{CONF_DIR}/gitea.key", key.read_text(), mode="0640")
    host.run(f"chown root:git {CONF_DIR}/gitea.key {CONF_DIR}/gitea.crt; systemctl start gitea", raise_on_failure=True)
    wait_for_gitea(host)
    version = installed_version(host)
    log(f"[{cfg.host}] Gitea {version} is up with the restored data")
    token = platform.read_secret("gitea-token")
    code = host.run(f"curl -sk --max-time 5 -o /dev/null -w '%{{http_code}}' -H 'Authorization: token {token}' https://127.0.0.1/api/v1/user").stdout.strip() if token else ""
    if code != "200":
        log("the token in secrets/gitea-token does not belong to this data; run `outpost gitea setup` to make a new one")
    state = platform.read_state("gitea") or {}
    platform.write_state("gitea", {**state, "name": cfg.name, "host": cfg.host, "version": version, "restored_from": archive.name,
                                   "restored_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    log(f"done. When everything looks right, remove {DATA_DIR}.before-restore-{stamp} on the server.")
