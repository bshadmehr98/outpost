"""What every command that touches the Gitea server shares: where things live and how to wait for it."""

GITEA_CLI = "runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini"
DATA_DIR = "/var/lib/gitea/data"
CONF_DIR = "/etc/gitea"


def wait_for_gitea(host, timeout: int = 90) -> None:
    result = host.run(
        f"for i in $(seq 1 {timeout // 2}); do curl -sk --max-time 3 https://127.0.0.1/api/v1/version >/dev/null 2>&1 && exit 0; sleep 2; done; "
        "journalctl -u gitea --no-pager -n 20; exit 1", check=False)
    if not result.ok:
        raise RuntimeError(f"Gitea did not start within {timeout}s:\n{result.stdout[-1500:]}")


def installed_version(host) -> str:
    """'28.2.0' or '' when Gitea is not installed."""
    out = host.run("/usr/local/bin/gitea --version 2>/dev/null || true").stdout.split()
    return out[2] if len(out) > 2 and out[0].lower() == "gitea" else ""
