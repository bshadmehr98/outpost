"""Steps 5 and 6 of `gitea setup`: the admin user, the API token Outpost uses, and the organisation."""

import secrets as pysecrets
import shlex
import time

from outpost import gitea  # gitea.connect through the package, so tests replace one name: outpost.gitea.connect
from outpost.gitea.service import GITEA_CLI, token_works
from outpost.gitea.setup.files import TOKEN_SCOPES
from outpost.system import AirGappedSystem
from outpost.workspace import GITEA_ADMIN_PASSWORD, GITEA_TOKEN


def ensure_admin_user(system: AirGappedSystem, host, log=print) -> None:
    """Create the admin user of servers.yml (gitea.user) if Gitea has none by that name.

    Its password goes into secrets/gitea-admin-password; one that is there already is used again.
    """
    user, gitea_name = system.servers.gitea.user, system.servers.gitea.name
    admins = host.run(f"{GITEA_CLI} admin user list --admin 2>/dev/null || true", raise_on_failure=True).stdout
    if user in admins.split():
        return
    password = system.secret(GITEA_ADMIN_PASSWORD) or pysecrets.token_urlsafe(18)
    host.run(
        f"{GITEA_CLI} admin user create --admin --username {shlex.quote(user)} --password {shlex.quote(password)} "
        f"--email {shlex.quote(user + '@' + gitea_name)} --must-change-password=false",
        raise_on_failure=True,
    )
    system.write_secret(GITEA_ADMIN_PASSWORD, password)
    log(f"[{host.alias}] created admin user {user} (password in secrets/gitea-admin-password)")


def ensure_api_token(system: AirGappedSystem, host, log=print) -> None:
    """Make secrets/gitea-token, with TOKEN_SCOPES, unless this Gitea accepts the one there already."""
    token = system.secret(GITEA_TOKEN)
    if token and token_works(host, token):
        return
    if token:
        log(f"[{host.alias}] this Gitea does not accept secrets/gitea-token (reinstalled?); making a new one")
    made = host.run(
        f"{GITEA_CLI} admin user generate-access-token --username {shlex.quote(system.servers.gitea.user)} "
        f"--token-name outpost-{int(time.time())} --scopes {TOKEN_SCOPES} --raw",
        raise_on_failure=True,
    )
    system.write_secret(GITEA_TOKEN, made.stdout.strip().splitlines()[-1])
    log(f"[{host.alias}] created API token (secrets/gitea-token; scopes {TOKEN_SCOPES})")


def ensure_org(system: AirGappedSystem, log=print) -> str:
    """Create the organisation of servers.yml (gitea.org) through the API. Returns Gitea's version."""
    org, alias = system.servers.gitea.org, system.servers.gitea.host
    with gitea.connect(system) as client:
        if client.ensure_org(org):
            log(f"[{alias}] created organisation {org}")
        version = client.version()
    log(f"[{alias}] gitea {version}, org {org}")
    return version
