"""The files only a real node gets: Docker's DNS and CA, the node's DNS server, and the shell profile."""

import json
from typing import TYPE_CHECKING

from outpost.client_config.paths import PATH_CA, PATH_COREFILE, PATH_NODE_RESOLV_CONF, PATH_PROFILE
from outpost.client_config.proxy_settings import proxy_exports

if TYPE_CHECKING:  # only for the type hints: endpoints imports PRIVATE_NETS from this package
    from outpost.endpoints import GiteaEndpoint, ProxyEndpoint


def node_files(
    gitea: "GiteaEndpoint", ca_pem: str, node_ip: str, proxy: "ProxyEndpoint | None" = None
) -> dict[str, str]:
    """The node-only files, as path -> content.

    With a proxy, profile.d also exports its variables. ~/.docker/config.json and /etc/environment are
    merged on the node instead of written whole (infra/node/proxy_settings.py).
    """
    return {
        # no registry-mirrors: images are referenced by their Gitea name, so a bare Docker Hub name fails at once
        "/etc/docker/daemon.json": json.dumps({"dns": [node_ip]}, indent=2) + "\n",
        f"/etc/docker/certs.d/{gitea.name}/ca.crt": ca_pem,
        PATH_COREFILE: corefile(node_ip),
        PATH_NODE_RESOLV_CONF: f"nameserver {node_ip}\n",
        PATH_PROFILE: profile_script(gitea, proxy),
    }


def corefile(node_ip: str) -> str:
    """CoreDNS's config: answer names from /etc/hosts, and forward the rest to the node's usual resolvers."""
    return (
        f".:53 {{\n"
        f"    bind {node_ip}\n"
        "    hosts /etc/hosts {\n        fallthrough\n    }\n"
        "    forward . /etc/resolv.conf\n"
        "    cache 30\n"
        "    reload\n"
        "    errors\n"
        "}\n"
    )


def profile_script(gitea: "GiteaEndpoint", proxy: "ProxyEndpoint | None" = None) -> str:
    """/etc/profile.d/outpost.sh: pip and npm settings for interactive shells, and the proxy's variables."""
    return (
        f"export PIP_INDEX_URL={gitea.pypi_index}\nexport PIP_CERT={PATH_CA}\n"
        f"export NPM_CONFIG_REGISTRY={gitea.npm_registry}\nexport NPM_CONFIG_CAFILE={PATH_CA}\n"
        + (proxy_exports(proxy) if proxy else "")
    )
