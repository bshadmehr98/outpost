"""`outpost pki rotate`: a new CA and Gitea certificate, pushed everywhere the old one went.

    rotate(system, log=print)

Three steps, logged as [1/3] to [3/3]: move the old CA aside and set Gitea up again (which makes a
new CA and certificate), build the config image with the new CA, and set up every infrastructure
again so its nodes trust the new CA.
"""

import shutil
import time

from outpost.endpoints import gitea_endpoint, proxy_endpoint_or_none
from outpost.gitea.connect import connect
from outpost.gitea.setup import setup as set_up_gitea
from outpost.gitea.upload import push_config_image
from outpost.infra.setup import set_up_infra
from outpost.system import AirGappedSystem
from outpost.workspace import CA_CERT, CA_KEY, GITEA_CERT, GITEA_KEY, STATE_UPLOAD

RESTART_RUNNER = "systemctl try-restart outpost-runner 2>/dev/null || true"


def rotate(system: AirGappedSystem, log=print) -> bool:
    """Rotate the CA and the Gitea certificate. True when every infrastructure verified afterwards.

    Raises:
        RuntimeError: Gitea has not been set up, so there is nothing to rotate.
    """
    if not system.gitea_is_set_up():
        raise RuntimeError("Gitea has not been set up; there is nothing to rotate. Run `outpost gitea setup`.")
    stamp = _archive_old_ca(system)
    log(
        f"==> [1/3] old CA and certificate moved to pki/archive/{stamp} and secrets/archive/{stamp}; "
        "making new ones and installing them on Gitea"
    )
    set_up_gitea(system.workspace, system.servers, log=log)
    log("==> [2/3] config image with the new CA")
    _push_config_image(system, log)
    all_verified = _set_up_infras_again(system, log)
    log(
        "rotation done. Images built before today still work; rebuild anything that baked the old pki/ca.crt into itself."
    )
    return all_verified


def _archive_old_ca(system: AirGappedSystem) -> str:
    """Move the CA and Gitea's certificate and keys to pki/archive/<stamp> and secrets/archive/<stamp>."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pki_dir, secrets_dir = system.workspace.pki_dir, system.workspace.secrets_dir
    old_pki, old_secrets = pki_dir / "archive" / stamp, secrets_dir / "archive" / stamp
    old_pki.mkdir(parents=True, exist_ok=True)
    old_secrets.mkdir(parents=True, exist_ok=True)
    for name, folder, archive in (
        (CA_CERT, pki_dir, old_pki),
        (GITEA_CERT, pki_dir, old_pki),
        (CA_KEY, secrets_dir, old_secrets),
        (GITEA_KEY, secrets_dir, old_secrets),
    ):
        if (folder / name).exists():
            shutil.move(folder / name, archive / name)
    return stamp


def _push_config_image(system: AirGappedSystem, log) -> None:
    uploaded = system.state(STATE_UPLOAD) or {}
    with connect(system.workspace, system.servers) as client:
        gitea = gitea_endpoint(system)
        push_config_image(
            client,
            gitea,
            system.ca_pem(),
            uploaded.get("distros", []),
            apk_branches=uploaded.get("apk_branches", []),
            rpm_distros=uploaded.get("rpm_distros", []),
            log=log,
            proxy=proxy_endpoint_or_none(system, gitea, log),
        )


def _set_up_infras_again(system: AirGappedSystem, log) -> bool:
    infras = system.set_up_infras()
    log(f"==> [3/3] nodes of {', '.join(infra.name for infra in infras) or 'no infrastructure yet'}: trust the new CA")
    all_verified = True
    for infra in infras:
        all_verified &= set_up_infra(system, infra, log=log)
        for node in system.nodes(infra.name):  # Docker and containerd read the CA file when they next connect
            node.run(RESTART_RUNNER)  # the runner needs a restart
    return all_verified
