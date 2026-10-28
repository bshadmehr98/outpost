"""A Gitea Actions runner on the first node of each infrastructure, so a `git push` can deploy.

    install_runner_for(system, ctx, host, log=print)    # install, register once, start

The runner is the official `gitea-runner` binary, installed from Gitea's generic packages and
registered in "host" mode: jobs run as shell steps directly on the node, which already has
Docker (or k3s) and already can reach nothing but Gitea. No job containers, no images to mirror,
no actions to vendor. A workflow says `runs-on: dev` to run on the dev node, `runs-on: docker`
to run on any node that has Docker.

  /usr/local/bin/gitea-runner             the binary
  /etc/outpost/runner.yaml                its config (work dir, no cache server)
  /var/lib/outpost-runner/.runner         registration (token from Gitea's admin API)
  outpost-runner.service                  the daemon; labels are passed on its command line

Jobs run as root, as the runner does: see "The Actions runner runs jobs as root" in
docs/reference/limits.md.
"""

import shlex

from outpost import gitea, versions
from outpost.client_config import RUNNER_HOME
from outpost.config import Servers
from outpost.infra import node
from outpost.infra.context import NodeContext
from outpost.ssh import Host
from outpost.system import AirGappedSystem

CONFIG = "/etc/outpost/runner.yaml"
BIN = "/usr/local/bin/gitea-runner"
UNIT_PATH = "/etc/systemd/system/outpost-runner.service"
REGISTRATION = f"{RUNNER_HOME}/.runner"

RUNNER_CONFIG = f"""# Written by Outpost. Jobs run on this host directly ("host" labels), in {RUNNER_HOME}/work.
log:
  level: info
runner:
  file: {REGISTRATION}
  capacity: 1
  timeout: 1h
cache:
  enabled: false
host:
  workdir_parent: {RUNNER_HOME}/work
"""

UNIT = f"""[Unit]
Description=Outpost Actions runner (Gitea Actions, jobs run on this host)
After=network-online.target outpost-egress.service docker.service k3s.service
Wants=network-online.target

[Service]
WorkingDirectory={RUNNER_HOME}
Environment=HOME={RUNNER_HOME}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={BIN} daemon --config {CONFIG} --labels {{labels}}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

START_RUNNER = (
    "systemctl daemon-reload && systemctl enable outpost-runner >/dev/null 2>&1; "
    "systemctl restart outpost-runner && sleep 1 && systemctl is-active outpost-runner"
)


def install_runner_for(system: AirGappedSystem, ctx: NodeContext, host: Host, log=print) -> dict:
    """A runner on host (the first node of an infrastructure), registered with the system's Gitea, so
    `outpost demo push` (or your own workflows) can deploy there."""
    with gitea.connect(system) as client:
        registration_token = client.runner_registration_token()
        registered = {runner["name"] for runner in client.runners()}
    return install(host, ctx, labels_for(system.servers, host.alias), registration_token, registered, log=log)


def labels_for(servers: Servers, alias: str) -> list[str]:
    """The labels a runner on `alias` offers: one per infrastructure whose first node it is,
    plus `docker` when any of those infrastructures gives it Docker."""
    mine = [i for i in servers.infrastructures.values() if i.nodes[0] == alias]
    labels = [f"{i.name}:host" for i in mine]
    if any(i.type in ("compose", "swarm") for i in mine):
        labels.append("docker:host")
    return labels


def install(
    host: Host, ctx: NodeContext, labels: list[str], registration_token: str, registered: set[str], log=print
) -> dict:
    """Install, register (once) and start the runner.

    Args:
        labels: what the runner offers (labels_for).
        registration_token: from Gitea's admin API; used only when the runner registers.
        registered: the runner names Gitea already knows. A node that was set up before is not
            registered twice, and one whose Gitea was reinstalled is registered again.

    Returns:
        {"node": alias, "labels": labels}
    """
    log(f"==> {host.alias}: Actions runner")
    _install_binary(host, ctx, log)
    host.run(f"mkdir -p {RUNNER_HOME}/work /etc/outpost", raise_on_failure=True)
    host.write(CONFIG, RUNNER_CONFIG)
    name = host.alias
    if name not in registered or not host.run(f"test -s {REGISTRATION}").ok:
        host.run(register_script(ctx.gitea.url, registration_token, name, labels), raise_on_failure=True)
        log(f"  [{host.alias}] registered runner {name} with Gitea")
    host.write(UNIT_PATH, UNIT.format(labels=shlex.quote(",".join(labels))))
    host.run(START_RUNNER, raise_on_failure=True)
    log(f"  [{host.alias}] actions runner active, labels: {', '.join(labels)}")
    return {"node": host.alias, "labels": labels}


def register_script(gitea_url: str, registration_token: str, name: str, labels: list[str]) -> str:
    """Shell that registers the runner with Gitea, from scratch."""
    return (
        f"systemctl stop outpost-runner 2>/dev/null || true\nrm -f {REGISTRATION}\ncd {RUNNER_HOME}\n"
        f"HOME={RUNNER_HOME} {BIN} register --config {CONFIG} --no-interactive --instance {gitea_url} "
        f"--token {shlex.quote(registration_token)} --name {shlex.quote(name)} --labels {shlex.quote(','.join(labels))}"
    )


def verifications() -> list[tuple[str, str]]:
    """What the first node runs on top of the node verifications. Both pass on a node without a runner."""
    return [
        ("actions runner active", f"test ! -s {REGISTRATION} || systemctl is-active outpost-runner"),
        (
            f"runner at pinned version {versions.RUNNER}",
            f"test ! -s {REGISTRATION} || {BIN} --version | grep -q {versions.RUNNER}",
        ),
    ]


def _install_binary(host: Host, ctx: NodeContext, log) -> None:
    """Install gitea-runner at the pinned version, unless it is there already."""
    installed = host.run(f"{BIN} --version 2>/dev/null || true", raise_on_failure=True).stdout
    if versions.RUNNER in installed:
        return
    log(f"  [{host.alias}] installing gitea-runner {versions.RUNNER} from Gitea")
    url = ctx.gitea.generic("gitea-runner", versions.RUNNER, "gitea-runner")
    host.run(
        f"systemctl stop outpost-runner 2>/dev/null || true\n{node.download_from_gitea(url, BIN)} && chmod 755 {BIN}",
        raise_on_failure=True,
    )
