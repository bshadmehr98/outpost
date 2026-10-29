"""Run smoke checks on the first node of an infrastructure, and record what they found."""

import shlex
import tarfile
import tempfile
from pathlib import Path

from outpost.checks.check import Check
from outpost.checks.load import load_checks
from outpost.config import InfraConfig
from outpost.endpoints import gitea_endpoint
from outpost.infra.proxy import probe_name, refused_name
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_PROXY, check_state

REMOTE_ROOT = "/tmp/outpost-checks"  # where a check's folder is copied on the node
LINES_SHOWN_ON_FAILURE = 8


def checks_for(system: AirGappedSystem, infra: InfraConfig, only: str | None = None) -> list[Check]:
    """The checks in the workspace that apply to an infrastructure's type; only the one named `only`, if given."""
    return [
        check
        for check in load_checks(system.workspace.checks_dir)
        if check.applies_to(infra.type) and (not only or check.name == only)
    ]


def run_checks(system: AirGappedSystem, infra: InfraConfig, checks: list[Check], log=print) -> list[dict]:
    """Run checks on the first node of an infrastructure and record them in state/check-<name>.json.

    A check that needs the egress proxy is skipped, with a message, while no proxy is set up.

    Returns:
        One result per check that ran.
    """
    gitea = gitea_endpoint(system)
    node = system.host(infra.nodes[0])
    env = {"GITEA": gitea.name, "ORG": gitea.org, **proxy_check_env(system)}
    results = []
    for check in checks:
        if "proxy" in check.requires and "PROXY_URL" not in env:
            log(
                f"check {check.name}: skipped, it needs the egress proxy and none is set up "
                "(egress_proxy in servers.yml, then `outpost proxy setup`)"
            )
            continue
        log(f"check {check.name} on {node.alias}: {check.description}")
        results.append(run_check(check, node, env, log=log))
    system.write_state(check_state(infra.name), {"results": results})
    return results


def proxy_check_env(system: AirGappedSystem) -> dict[str, str]:
    """PROXY_URL, PROXY_ALLOWED (a name the proxy lets through) and PROXY_REFUSED (one it must refuse) for the
    checks, from state/proxy.json; empty when no proxy is configured or it was never set up."""
    state = system.state(STATE_PROXY) or {}
    if not system.servers.egress_proxy or not state.get("url") or not state.get("allow"):
        return {}
    return {
        "PROXY_URL": state["url"],
        "PROXY_ALLOWED": probe_name(state["allow"]),
        "PROXY_REFUSED": refused_name(state["allow"]),
    }


def run_check(check: Check, host, env: dict[str, str], log=print, timeout: int = 900) -> dict:
    """Copy a check's folder to host and run its steps there, with env set; stop at the first failing step.

    Returns:
        {"name", "node", "ok", "steps": [{"run", "ok", "output"}]}
    """
    remote_dir = f"{REMOTE_ROOT}/{check.name}"
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    host.run(f"rm -rf {remote_dir} && mkdir -p {remote_dir}", raise_on_failure=True)
    if check.context:
        _copy_folder(host, check.context, remote_dir)
    steps = []
    for step in check.steps:
        result = host.run(f"export {exports}\ncd {remote_dir}\n{step['run']}", timeout=timeout)
        output = (result.stdout + result.stderr).strip()
        passed = result.ok and (step["expect"] is None or step["expect"] in output)
        steps.append({"run": step["run"], "ok": passed, "output": output[-2000:]})
        log(f"  {'ok  ' if passed else 'FAIL'} {step['run']}")
        if not passed:
            for line in output.splitlines()[-LINES_SHOWN_ON_FAILURE:]:
                log(f"       | {line}")
            break
    host.run(f"rm -rf {remote_dir}")
    return {"name": check.name, "node": host.alias, "ok": all(step["ok"] for step in steps), "steps": steps}


def _copy_folder(host, folder: Path, remote_dir: str) -> None:
    """Copy folder's content into remote_dir on host, as one tar.gz."""
    archive = _tar_folder(folder)
    try:
        host.put(archive, f"{remote_dir}/context.tgz")
    finally:
        archive.unlink(missing_ok=True)
    host.run(f"tar -xzf {remote_dir}/context.tgz -C {remote_dir} && rm {remote_dir}/context.tgz", raise_on_failure=True)


def _tar_folder(folder: Path) -> Path:
    with tempfile.NamedTemporaryFile(prefix="outpost-check-", suffix=".tgz", delete=False) as tmp:
        archive = Path(tmp.name)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(folder, arcname=".")
    return archive
