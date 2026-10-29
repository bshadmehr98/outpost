"""How the laptop reaches Gitea's API: an ssh tunnel to the Gitea server's port 443.

    with gitea.connect(system) as client:     # a GiteaClient, logged in as the outpost admin user
        client.version()

We never assume the laptop can resolve or route to the Gitea name. ssh already works (it is how
everything else is done), so the API rides on it too.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from outpost.gitea.client import GiteaClient
from outpost.http import Http
from outpost.ssh import Tunnel
from outpost.system import AirGappedSystem
from outpost.workspace import GITEA_TOKEN


@contextmanager
def connect(system: AirGappedSystem) -> Iterator[GiteaClient]:
    """A GiteaClient for the system's Gitea, through a tunnel that is closed when the `with` ends.

    Raises:
        RuntimeError: secrets/gitea-token is missing.
        Unreachable: the tunnel could not be opened.
    """
    gitea = system.servers.gitea
    token = api_token(system)
    with Tunnel(gitea.host, 443, config_file=system.ssh_config) as tunnel:
        http = Http(
            f"https://127.0.0.1:{tunnel.port}",
            ca_file=system.workspace.ca_cert,
            verify_hostname=False,  # the tunnel already reached the right server
            host_header=gitea.name,
            basic=(gitea.user, token),
        )
        yield GiteaClient(http, gitea.org, public_url=gitea.url)


def api_token(system: AirGappedSystem) -> str:
    """The API token `gitea setup` made (secrets/gitea-token).

    Raises:
        RuntimeError: it is missing.
    """
    token = system.secret(GITEA_TOKEN)
    if not token:
        raise RuntimeError("secrets/gitea-token is missing. Run `outpost gitea setup` first.")
    return token
