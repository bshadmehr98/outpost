"""Steps 5 and 6 of `gitea setup`: the admin user, the API token Outpost uses, and the organisation."""

import json
import re
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
    The password never goes on a command line that `ps` shows on the server: Gitea makes a random
    one and prints it, and a saved one is set back through the API, with curl reading it on stdin.
    """
    user, gitea_name = system.servers.gitea.user, system.servers.gitea.name
    admins = host.run(f"{GITEA_CLI} admin user list --admin 2>/dev/null || true", raise_on_failure=True).stdout
    if user in admins.split():
        return
    created = host.run(
        f"{GITEA_CLI} admin user create --admin --username {shlex.quote(user)} "
        f"--random-password --random-password-length 24 "
        f"--email {shlex.quote(user + '@' + gitea_name)} --must-change-password=false",
        raise_on_failure=True,
    )
    password = _random_password_in(created.stdout)
    saved = system.secret(GITEA_ADMIN_PASSWORD)
    if saved:
        _change_password(host, user, old=password, new=saved)
        password = saved
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


# -- the admin password, off the command line -------------------------------------------

_REQUEST_END = "OUTPOST_CURL_REQUEST"  # ends the here-document that hands curl its request
_RANDOM_PASSWORD = re.compile(r"generated random password is '(.+)'")


def _random_password_in(output: str) -> str:
    """The password that `gitea admin user create --random-password` printed."""
    found = _RANDOM_PASSWORD.search(output)
    if not found:
        raise RuntimeError(f"gitea admin user create printed no password:\n{output.strip()[-300:]}")
    return found.group(1)


def _change_password(host, user: str, old: str, new: str) -> None:
    """Set user's password to new through Gitea's admin API, logging in with old.

    curl reads its whole request from stdin (`-K -`, a here-document), so neither password is on
    its command line.
    """
    body = json.dumps({"login_name": user, "source_id": 0, "password": new, "must_change_password": False})
    request = "\n".join(
        [
            f"url = {_curl_quoted(f'https://127.0.0.1/api/v1/admin/users/{user}')}",
            'request = "PATCH"',
            f"user = {_curl_quoted(f'{user}:{old}')}",
            'header = "Content-Type: application/json"',
            f"data = {_curl_quoted(body)}",
        ]
    )
    result = host.run(
        f"curl -sk --max-time 10 -o /dev/null -w '%{{http_code}}' -K - <<'{_REQUEST_END}'\n{request}\n{_REQUEST_END}",
        raise_on_failure=True,
    )
    if result.stdout.strip() != "200":
        raise RuntimeError(f"[{host.alias}] Gitea refused the saved admin password (HTTP {result.stdout.strip()})")


def _curl_quoted(text: str) -> str:
    """text as a double-quoted value in a curl config file."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
