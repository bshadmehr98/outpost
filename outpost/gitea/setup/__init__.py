"""`outpost gitea setup`: install Gitea on its server over ssh. Safe to run again after changing servers.yml.

    gitea.setup(system, log=print)     # {"name", "ip", "version"}

What ends up on the server:
  /usr/local/bin/gitea                 the binary from the box
  /etc/gitea/app.ini                   config: HTTPS on 443, SQLite, packages on, registration off
  /etc/gitea/gitea.crt, gitea.key      certificate signed by your CA (pki/ca.crt)
  /var/lib/gitea/                      data, repositories, packages
  /etc/systemd/system/gitea.service    runs as user `git`
  /usr/local/sbin/outpost-egress       the server is sealed once Gitea is up

What ends up on your laptop:
  secrets/gitea-token                  the token Outpost uses (packages, organisation, repositories; not admin)
  secrets/gitea-admin-password         so you can log in to the web UI
  state/gitea.json                     name, ip, version

Upgrades: when versions.GITEA is newer than what runs on the server, setup takes a backup into
backups/ first, then replaces the binary; Gitea migrates its database on the next start.

Seven steps, logged as `==> [n/7]` (the dashboard shows them as progress):

    server.py           1, 2  connect_and_check_arch, ensure_certificates
    binary.py           3     check_box_has_gitea, install_binary (backup first), ensure_git
    service_config.py   3, 4  make_git_user_and_folders, write_certificate, write_app_ini, start_gitea
    admin.py            5, 6  ensure_admin_user, ensure_api_token, ensure_org
    seal.py             7     seal_gitea_server
    files.py                  APP_INI, UNIT, TOKEN_SCOPES
"""

from outpost import hostsfile, versions
from outpost.gitea.setup.admin import ensure_admin_user, ensure_api_token, ensure_org
from outpost.gitea.setup.binary import check_box_has_gitea, ensure_git, install_binary
from outpost.gitea.setup.files import APP_INI, TOKEN_SCOPES, UNIT
from outpost.gitea.setup.seal import seal_gitea_server
from outpost.gitea.setup.server import connect_and_check_arch, ensure_certificates
from outpost.gitea.setup.service_config import (
    make_git_user_and_folders,
    start_gitea,
    write_app_ini,
    write_certificate,
)
from outpost.hosts import inspect_and_record
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_GITEA

__all__ = ["APP_INI", "TOKEN_SCOPES", "UNIT", "setup"]


def setup(system: AirGappedSystem, log=print) -> dict:
    """Install or update Gitea on the Gitea server, and seal the server.

    Returns:
        {"name", "ip", "version"} of the Gitea that answers now.

    Raises:
        RuntimeError: the box has no Gitea, the server is of the wrong CPU type, or Gitea did not start.
        SshError: the server could not be reached, or a step failed on it.
    """
    gitea_config, host = system.servers.gitea, system.gitea_host
    binary = check_box_has_gitea(system)

    log(f"==> [1/7] connecting to {gitea_config.host}")
    ip = connect_and_check_arch(host, gitea_config, log)

    log("==> [2/7] certificates")
    cert, key = ensure_certificates(system, ip)

    log(f"==> [3/7] gitea {versions.GITEA} binary")
    install_binary(system, host, binary, log)
    ensure_git(host, log)
    make_git_user_and_folders(host)
    write_certificate(host, cert, key)
    hostsfile.keep_names(host, [(ip, gitea_config.name)])  # now, and again at every boot

    log("==> [4/7] configuration and service")
    write_app_ini(host, gitea_config.name, log)
    start_gitea(host, gitea_config.name, log)

    log("==> [5/7] admin user and token")
    ensure_admin_user(system, host, log)
    ensure_api_token(system, host, log)
    _record_gitea(system, ip)

    log(f"==> [6/7] organisation {gitea_config.org}")
    version = ensure_org(system, log)

    log("==> [7/7] sealing the server")
    seal_gitea_server(system, host, ip, log)
    inspect_and_record(system, host)
    return {"name": gitea_config.name, "ip": ip, "version": version}


def _record_gitea(system: AirGappedSystem, ip: str) -> None:
    """state/gitea.json: what nodes need to reach Gitea, written before the API is first used."""
    gitea_config = system.servers.gitea
    system.write_state(
        STATE_GITEA,
        {
            "name": gitea_config.name,
            "ip": ip,
            "org": gitea_config.org,
            "user": gitea_config.user,
            "version": versions.GITEA,
            "host": gitea_config.host,
        },
    )
