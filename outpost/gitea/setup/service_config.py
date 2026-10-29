"""Steps 3 and 4 of `gitea setup`: the git user, its folders, the certificate, app.ini and the service."""

from pathlib import Path

from outpost.gitea.service import CONF_DIR, wait_for_gitea
from outpost.gitea.setup.files import APP_INI, UNIT

APP_INI_PATH = f"{CONF_DIR}/app.ini"
UNIT_PATH = "/etc/systemd/system/gitea.service"
SECRETS_IN_APP_INI = ("SECRET_KEY", "INTERNAL_TOKEN", "JWT_SECRET", "LFS_JWT_SECRET")


def make_git_user_and_folders(host) -> None:
    """The system user `git` that runs Gitea, and the folders it owns."""
    host.run(
        "id git >/dev/null 2>&1 || useradd --system --shell /bin/bash --home /var/lib/gitea --create-home git\n"
        f"mkdir -p /var/lib/gitea/{{custom,data,log}} {CONF_DIR}\n"
        "chown -R git:git /var/lib/gitea && chmod 750 /var/lib/gitea\n",
        raise_on_failure=True,
    )


def write_certificate(host, cert: Path, key: Path) -> None:
    """Gitea's certificate and key, signed by the CA in pki/."""
    host.write(f"{CONF_DIR}/gitea.crt", cert.read_text())
    host.write(f"{CONF_DIR}/gitea.key", key.read_text(), mode="0640")


def write_app_ini(host, gitea_name: str, log=print) -> None:
    """Write app.ini once, with secrets generated on the server; an existing one is kept as it is."""
    if host.run(f"test -s {APP_INI_PATH}").ok:
        return
    log(f"[{host.alias}] writing {APP_INI_PATH}")
    host.write(APP_INI_PATH, APP_INI.format(name=gitea_name), mode="0640")
    fill_in_secrets = "".join(
        f'sed -i "s|__{name}__|$(/usr/local/bin/gitea generate secret {name})|" {APP_INI_PATH}\n'
        for name in SECRETS_IN_APP_INI
    )
    host.run(fill_in_secrets, raise_on_failure=True)


def start_gitea(host, gitea_name: str, log=print) -> None:
    """Write the unit, (re)start Gitea, and wait until it answers.

    Raises:
        RuntimeError: Gitea did not start.
    """
    # Gitea occasionally saves generated settings back into app.ini, so the git user owns it
    host.run(
        f"chown git:git {APP_INI_PATH} && chmod 640 {APP_INI_PATH} && chown root:git {CONF_DIR}/gitea.key {CONF_DIR}/gitea.crt",
        raise_on_failure=True,
    )
    host.write(UNIT_PATH, UNIT)
    host.run(
        "systemctl daemon-reload && systemctl enable gitea >/dev/null 2>&1; systemctl restart gitea",
        raise_on_failure=True,
    )
    wait_for_gitea(host)
    log(f"[{host.alias}] gitea is answering on https://{gitea_name}")
