"""Steps 2 and 3 of a node: write the client files, trust the CA, and prove Gitea answers over trusted TLS."""

from outpost import client_config
from outpost.client_config import PATH_CA
from outpost.infra.context import NodeContext
from outpost.ssh import Host

# Debian and Alpine both ship update-ca-certificates reading /usr/local/share/ca-certificates (Alpine's
# comes with the ca-certificates package); the RHEL family has update-ca-trust and its anchors folder.
ADD_CA_TO_TRUST_STORE = (
    "if command -v update-ca-certificates >/dev/null; then "
    f"  cp {PATH_CA} /usr/local/share/ca-certificates/outpost-ca.crt && update-ca-certificates >/dev/null; "
    "elif command -v update-ca-trust >/dev/null; then "
    f"  cp {PATH_CA} /etc/pki/ca-trust/source/anchors/outpost-ca.crt && update-ca-trust; fi"
)


def write_client_files(host: Host, ctx: NodeContext, node_ip: str) -> None:
    """Write the files that point pip, npm, apt and Docker at Gitea, and the node-only ones (client_config).

    The package sources are written later, by configure_os_packages, once the node knows what Gitea has.
    """
    files = {
        **client_config.client_files(ctx.gitea, ctx.ca_pem, apt_key=None, distros=[], proxy=ctx.proxy),
        **client_config.node_files(ctx.gitea, ctx.ca_pem, node_ip, proxy=ctx.proxy),
    }
    for path, content in files.items():
        host.write(path, content)


def trust_ca(host: Host) -> None:
    """Add the CA to the system trust store, so every program that uses it trusts Gitea."""
    host.run(ADD_CA_TO_TRUST_STORE, raise_on_failure=True)


def check_reaches_gitea(host: Host, ctx: NodeContext, log=print) -> None:
    """Ask Gitea for its version, over https with the CA.

    Raises:
        RuntimeError: the node cannot reach Gitea, with curl's error.
    """
    url = ctx.gitea.url
    reach = host.run(f"curl --cacert {PATH_CA} -fsS --max-time 10 {url}/api/v1/version")
    if not reach.ok:
        raise RuntimeError(
            f"[{host.alias}] cannot reach {url}: {reach.stderr.strip()}. Check servers.yml gitea.ip and firewalls (port 443)."
        )
    log(f"  [{host.alias}] reaches Gitea {reach.stdout.strip()}")
