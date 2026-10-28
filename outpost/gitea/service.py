"""What every command that touches the Gitea server shares: where things live, and how to ask Gitea.

Setup, backup and restore use these on the Gitea server's Host:

    wait_for_gitea(host)            until https://127.0.0.1 answers on the server
    installed_version(host)         "28.2.0", or "" when Gitea is not installed
    token_works(host, token)        does this Gitea accept the API token?
"""

GITEA_CLI = "runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini"  # gitea's admin CLI, as user git
DATA_DIR = "/var/lib/gitea/data"
CONF_DIR = "/etc/gitea"

TOKEN_HEADER_END = "OUTPOST_TOKEN_HEADER"  # ends the here-document that hands curl the token


def wait_for_gitea(host, timeout: int = 90) -> None:
    """Wait until Gitea answers on the server's port 443.

    Raises:
        RuntimeError: it did not answer within timeout seconds; the message has the end of its journal.
    """
    result = host.run(
        f"for i in $(seq 1 {timeout // 2}); do curl -sk --max-time 3 https://127.0.0.1/api/v1/version >/dev/null 2>&1 && exit 0; sleep 2; done; "
        "journalctl -u gitea --no-pager -n 20; exit 1"
    )
    if not result.ok:
        raise RuntimeError(f"Gitea did not start within {timeout}s:\n{result.stdout[-1500:]}")


def installed_version(host) -> str:
    """'28.2.0' or '' when Gitea is not installed."""
    out = host.run("/usr/local/bin/gitea --version 2>/dev/null || true", raise_on_failure=True).stdout.split()
    return out[2] if len(out) > 2 and out[0].lower() == "gitea" else ""


def token_works(host, token: str) -> bool:
    """True when the Gitea on host accepts token for its API.

    The token reaches curl on stdin (`-H @-`), in a here-document, so it is never on a command line
    that `ps` shows to the other users of the server.
    """
    result = host.run(
        "curl -sk --max-time 5 -o /dev/null -w '%{http_code}' -H @- https://127.0.0.1/api/v1/user "
        f"<<'{TOKEN_HEADER_END}'\nAuthorization: token {token}\n{TOKEN_HEADER_END}"
    )
    return result.stdout.strip() == "200"
