"""How the laptop reaches Gitea's API: an SSH tunnel to the Gitea host's port 443.

We never assume the laptop can resolve or route to the Gitea name. SSH already works
(it is how everything else is done), so the API rides on it too.
"""

from contextlib import contextmanager

from ..config import Servers
from ..http import Http
from ..ssh import Tunnel
from ..workspace import Workspace
from .client import GiteaClient


def token_for(workspace: Workspace) -> str:
    token = workspace.read_secret("gitea-token")
    if not token:
        raise RuntimeError("secrets/gitea-token is missing. Run `outpost gitea setup` first.")
    return token


@contextmanager
def connect(workspace: Workspace, servers: Servers):
    """`with connect(workspace, servers) as client:` talks to Gitea as the outpost admin user."""
    cfg = servers.gitea
    token = token_for(workspace)
    with Tunnel(cfg.host, 443) as tunnel:
        http = Http(
            f"https://127.0.0.1:{tunnel.port}",
            ca_file=workspace.pki_dir / "ca.crt",
            verify_hostname=False,
            host_header=cfg.name,
            basic=(cfg.user, token),
        )
        yield GiteaClient(http, cfg.org, public_url=cfg.url)
