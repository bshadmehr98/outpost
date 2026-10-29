"""The egress proxy's settings, in the forms programs read them.

Programs find the proxy through the de facto standard HTTPS_PROXY, HTTP_PROXY and NO_PROXY variables,
upper and lower case: curl reads only `http_proxy` in lower case, and busybox only the lower-case names.

    /etc/outpost/proxy.env        KEY=value lines, for `env_file:` in Compose and Swarm files and for scripts
    /etc/profile.d/outpost.sh     the same as exports, for interactive shells (node_files.profile_script)
    /etc/environment              the same, for PAM sessions (ssh logins)
    ~/.docker/config.json         `proxies.default` (httpProxy, httpsProxy, noProxy): the Docker CLI puts
                                  these into every container it creates with `docker run` / `docker create`
                                  and into every `docker build` as the predefined proxy build arguments,
                                  https://docs.docker.com/engine/cli/proxy/ ("Configure the Docker client").
                                  Written for root and for the Actions runner's HOME, so jobs get it too.
"""

import json
import shlex
from typing import TYPE_CHECKING

from outpost.client_config.paths import PATH_ENVIRONMENT

if TYPE_CHECKING:  # only for the type hints: endpoints imports PRIVATE_NETS from this package
    from outpost.endpoints import ProxyEndpoint

NO_PROXY_ENV = "# No egress proxy is configured (servers.yml has no egress_proxy). Kept so env_file: can name it.\n"
ENVIRONMENT_MARKER = "# Outpost egress proxy"  # the line before the proxy lines Outpost puts in /etc/environment


def proxy_env(proxy: "ProxyEndpoint") -> str:
    """/etc/outpost/proxy.env: KEY=value lines for `env_file:` and `set -a; . file`.

    OUTPOST_PROXY_ALLOWED is for information (the demo app probes those names); the proxy enforces the
    list, not the client.
    """
    lines = [f"{k}={v}" for k, v in proxy.env().items()] + [f"OUTPOST_PROXY_ALLOWED={','.join(proxy.allowed)}"]
    return (
        "# Written by Outpost: the egress proxy. Programs that honour these variables reach the names in\n"
        "# OUTPOST_PROXY_ALLOWED through it; everything else still has no internet.\n" + "\n".join(lines) + "\n"
    )


def proxy_exports(proxy: "ProxyEndpoint") -> str:
    """The proxy variables as `export KEY=value` lines, for a shell profile."""
    return "".join(f"export {k}={v}\n" for k, v in proxy.env().items())


def docker_client_config(existing: str, proxy: "ProxyEndpoint | None", gitea_name: str) -> str | None:
    """~/.docker/config.json with `proxies.default` set (or removed), and everything else kept (the auths
    from `docker login`).

    Without a proxy, only a `default` entry Outpost wrote is removed: its noProxy starts with the Gitea name.

    Returns:
        The new file, or None when nothing has to change or the file is not JSON we can safely edit.
    """
    try:
        data = json.loads(existing) if existing.strip() else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    proxies = data.get("proxies")
    proxies = proxies if isinstance(proxies, dict) else {}
    if proxy:
        want = {"httpProxy": proxy.url, "httpsProxy": proxy.url, "noProxy": proxy.no_proxy}
        if proxies.get("default") == want:
            return None
        proxies["default"] = want
        data["proxies"] = proxies
    else:
        mine = proxies.get("default") or {}
        if not str(mine.get("noProxy", "")).startswith(gitea_name + ","):
            return None
        del proxies["default"]
        if not proxies:
            data.pop("proxies", None)
    return json.dumps(data, indent=2) + "\n"


def environment_script(proxy: "ProxyEndpoint | None") -> str:
    """Shell that puts the proxy lines into /etc/environment, after removing earlier ones.

    pam_env reads KEY=value lines, with no `export`. The file is rewritten in place. Without a proxy, the
    script only removes what Outpost wrote.
    """
    remove_proxy_lines = (
        f"grep -vE '^({ENVIRONMENT_MARKER}|HTTPS?_PROXY=|https?_proxy=|NO_PROXY=|no_proxy=)' {PATH_ENVIRONMENT} || true"
    )
    new_file = f"{PATH_ENVIRONMENT}.outpost"
    replace_in_place = f"cat {new_file} > {PATH_ENVIRONMENT} && rm -f {new_file}"
    if proxy:
        echo_proxy_lines = "".join(f"echo {shlex.quote(f'{k}={v}')}; " for k, v in proxy.env().items())
        return (
            f"touch {PATH_ENVIRONMENT}\n"
            f"{{ {remove_proxy_lines}; echo '{ENVIRONMENT_MARKER}'; {echo_proxy_lines}}} > {new_file} && {replace_in_place}\n"
        )
    return (
        f"if grep -qs '^{ENVIRONMENT_MARKER}' {PATH_ENVIRONMENT}; then {{ {remove_proxy_lines}; }} > {new_file} && "
        f"{replace_in_place}; fi\n"
    )
