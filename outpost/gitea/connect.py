"""How the laptop reaches Gitea's API: an SSH tunnel to the Gitea host's port 443.

We never assume the laptop can resolve or route to the Gitea name. SSH already works
(it is how everything else is done), so the API rides on it too.
"""

from contextlib import contextmanager

from ..config import Servers
from ..http import Http
from ..infra.clients import GiteaInfo
from ..paths import Platform
from ..ssh import Tunnel
from .client import GiteaClient


def gitea_info(platform: Platform, servers: Servers) -> GiteaInfo:
    state = platform.read_state("gitea") or {}
    ip = servers.gitea.ip or state.get("ip")
    if not ip:
        raise RuntimeError("Gitea's IP is unknown. Run `outpost gitea setup` first or set gitea.ip in servers.yml.")
    return GiteaInfo(name=servers.gitea.name, ip=ip, org=servers.gitea.org)


def token_for(platform: Platform) -> str:
    token = platform.read_secret("gitea-token")
    if not token:
        raise RuntimeError("secrets/gitea-token is missing. Run `outpost gitea setup` first.")
    return token


@contextmanager
def connect(platform: Platform, servers: Servers):
    """`with connect(platform, servers) as client:` talks to Gitea as the outpost admin user."""
    cfg = servers.gitea
    token = token_for(platform)
    with Tunnel(cfg.host, 443) as tunnel:
        http = Http(
            f"https://127.0.0.1:{tunnel.port}",
            ca_file=platform.pki_dir / "ca.crt",
            verify_hostname=False,
            host_header=cfg.name,
            basic=(cfg.user, token),
        )
        yield GiteaClient(http, cfg.org, public_url=cfg.url)
