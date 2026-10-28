"""Where the demo runs, and what it is doing: the URLs per infrastructure, the workflow runs, the health."""

import json

from outpost import gitea  # gitea.connect through the package, so tests replace one name: outpost.gitea.connect
from outpost.demo.project import NAME
from outpost.demo.workflow import PORTS
from outpost.endpoints import GiteaEndpoint, gitea_endpoint
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_DEMO_STATUS, infra_state

WORKFLOW_RUNS_SHOWN = 3


def demo_urls(system: AirGappedSystem) -> dict[str, dict]:
    """Per infrastructure: the node, the port and the URL you open, from what setup recorded."""
    out = {}
    for name, infra in system.servers.infrastructures.items():
        state = system.state(infra_state(name)) or {}
        node = infra.nodes[0]
        ip = ((state.get("nodes") or {}).get(node) or {}).get("ip")
        out[name] = {
            "type": infra.type,
            "node": node,
            "ip": ip,
            "port": PORTS[infra.type],
            "url": f"http://{ip}:{PORTS[infra.type]}" if ip else None,
            "set_up": bool(state),
        }
    return out


def status(system: AirGappedSystem) -> dict:
    """The latest workflow runs with their jobs, and what each node serves on the demo port.

    Also written to state/demo-status.json: the proof report reads it, with no ssh needed there.
    """
    endpoint = gitea_endpoint(system)
    result = {
        "repo": f"{endpoint.org}/{NAME}",
        "actions_url": f"https://{endpoint.ip}/{endpoint.org}/{NAME}/actions",
        "runs": _workflow_runs(system, endpoint),
        "serving": _what_is_served(system),
    }
    system.write_state(STATE_DEMO_STATUS, result)
    return result


def _workflow_runs(system: AirGappedSystem, endpoint: GiteaEndpoint) -> list[dict]:
    """The latest runs of the demo's workflow, with links that work from the laptop (by IP)."""
    runs = []
    with gitea.connect(system) as client:
        for run in client.workflow_runs(endpoint.org, NAME, limit=WORKFLOW_RUNS_SHOWN):
            jobs = client.workflow_jobs(endpoint.org, NAME, run["id"])
            runs.append(
                {
                    "id": run["id"],
                    "sha": run.get("head_sha", "")[:10],
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "url": run.get("html_url", "").replace(f"https://{endpoint.name}", f"https://{endpoint.ip}"),
                    "when": run.get("created_at"),
                    "jobs": [
                        {"name": j["name"], "status": j.get("status"), "conclusion": j.get("conclusion")} for j in jobs
                    ],
                }
            )
    return runs


def _what_is_served(system: AirGappedSystem) -> dict[str, dict]:
    """Per set-up infrastructure: its URL, and the demo's /healthz answer on its first node (None if none)."""
    serving = {}
    for name, where in demo_urls(system).items():
        if not where["set_up"]:
            continue
        answer = system.host(where["node"]).run(f"curl -fsS --max-time 5 http://127.0.0.1:{where['port']}/healthz")
        try:
            health = json.loads(answer.stdout) if answer.ok else None
        except ValueError:
            health = None
        serving[name] = {**where, "health": health}
    return serving
