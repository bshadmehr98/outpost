"""A Gitea Actions runner on the first node of each infrastructure, so a `git push` can deploy.

The runner is the official `gitea-runner` binary, installed from Gitea's generic packages and
registered in "host" mode: jobs run as shell steps directly on the node, which already has
Docker (or k3s) and already can reach nothing but Gitea. No job containers, no images to mirror,
no actions to vendor. A workflow says `runs-on: dev` to run on the dev node, `runs-on: docker`
to run on any node that has Docker.

  /usr/local/bin/gitea-runner             the binary
  /etc/outpost/runner.yaml                its config (work dir, no cache server)
  /var/lib/outpost-runner/.runner         registration (token from Gitea's admin API)
  outpost-runner.service                  the daemon; labels are passed on its command line
"""

import shlex

from .. import versions
from . import common

HOME = "/var/lib/outpost-runner"
CONFIG = "/etc/outpost/runner.yaml"
BIN = "/usr/local/bin/gitea-runner"

RUNNER_CONFIG = f"""# Written by Outpost. Jobs run on this host directly ("host" labels), in {HOME}/work.
log:
  level: info
runner:
  file: {HOME}/.runner
  capacity: 1
  timeout: 1h
cache:
  enabled: false
host:
  workdir_parent: {HOME}/work
"""

UNIT = f"""[Unit]
Description=Outpost Actions runner (Gitea Actions, jobs run on this host)
After=network-online.target outpost-egress.service docker.service k3s.service
Wants=network-online.target

[Service]
WorkingDirectory={HOME}
Environment=HOME={HOME}
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={BIN} daemon --config {CONFIG} --labels {{labels}}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def labels_for(servers, alias: str) -> list[str]:
    """The labels a runner on `alias` offers: one per infrastructure whose first node it is,
    plus `docker` when any of those infrastructures gives it Docker."""
    mine = [i for i in servers.infrastructures.values() if i.nodes[0] == alias]
    labels = [f"{i.name}:host" for i in mine]
    if any(i.type in ("compose", "swarm") for i in mine):
        labels.append("docker:host")
    return labels


def install(host, ctx: common.NodeContext, labels: list[str], registration_token: str, registered: set[str], log=print) -> dict:
    """Install, register (once) and start the runner. `registered` holds the runner names Gitea
    already knows, so a node that was set up before is not registered twice, and one whose Gitea
    was reinstalled is registered again."""
    g = ctx.gitea
    url = g.generic("gitea-runner", versions.RUNNER, "gitea-runner")
    log(f"==> {host.alias}: Actions runner")
    if versions.RUNNER not in host.run(f"{BIN} --version 2>/dev/null || true", raise_on_failure=True).stdout:
        log(f"  [{host.alias}] installing gitea-runner {versions.RUNNER} from Gitea")
        host.run(f"systemctl stop outpost-runner 2>/dev/null || true\n{common.curl(ctx, url, BIN)} && chmod 755 {BIN}", raise_on_failure=True)
    host.run(f"mkdir -p {HOME}/work /etc/outpost", raise_on_failure=True)
    host.write(CONFIG, RUNNER_CONFIG)
    name = host.alias
    if name not in registered or not host.run(f"test -s {HOME}/.runner").ok:
        host.run(
            f"systemctl stop outpost-runner 2>/dev/null || true\nrm -f {HOME}/.runner\ncd {HOME}\n"
            f"HOME={HOME} {BIN} register --config {CONFIG} --no-interactive --instance {g.url} "
            f"--token {shlex.quote(registration_token)} --name {shlex.quote(name)} --labels {shlex.quote(','.join(labels))}", raise_on_failure=True)
        log(f"  [{host.alias}] registered runner {name} with Gitea")
    host.write("/etc/systemd/system/outpost-runner.service", UNIT.format(labels=shlex.quote(",".join(labels))))
    host.run("systemctl daemon-reload && systemctl enable outpost-runner >/dev/null 2>&1; systemctl restart outpost-runner && sleep 1 && systemctl is-active outpost-runner", raise_on_failure=True)
    log(f"  [{host.alias}] actions runner active, labels: {', '.join(labels)}")
    return {"node": host.alias, "labels": labels}


def extra_checks() -> list[tuple[str, str]]:
    return [("actions runner active", f"test ! -s {HOME}/.runner || systemctl is-active outpost-runner"),
            (f"runner at pinned version {versions.RUNNER}", f"test ! -s {HOME}/.runner || {BIN} --version | grep -q {versions.RUNNER}")]
