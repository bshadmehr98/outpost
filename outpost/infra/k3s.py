"""k3s: a small Kubernetes. The first node is the server, the rest are agents.

k3s ships an "airgap images" tarball with everything the cluster itself needs; we place it
where k3s looks and tell containerd to fetch application images from Gitea.
"""

from .. import versions
from . import clients, common

IMAGES_DIR = "/var/lib/rancher/k3s/agent/images"


def registries_yaml(ctx: common.NodeContext) -> str:
    g = ctx.gitea
    return (
        "mirrors:\n"
        "  docker.io:\n"
        f"    endpoint:\n      - \"{g.url}\"\n"
        f"  \"{g.name}\":\n"
        f"    endpoint:\n      - \"{g.url}\"\n"
        "configs:\n"
        f"  \"{g.name}\":\n"
        f"    tls:\n      ca_file: {clients.PATH_CA}\n"
    )


def _install(host, ctx: common.NodeContext, exec_args: str, env: str, log) -> None:
    g = ctx.gitea
    if versions.K3S.split("+")[0] in host.run("k3s --version 2>/dev/null | head -1 || true").stdout:
        log(f"  [{host.alias}] k3s {versions.K3S} already installed")
        return
    log(f"==> {host.alias}: installing k3s {versions.K3S} from Gitea")
    host.write("/etc/rancher/k3s/registries.yaml", registries_yaml(ctx))
    host.run(
        f"mkdir -p {IMAGES_DIR}\n"
        f"{common.curl(ctx, g.generic('k3s', versions.K3S, versions.names()['k3s']), '/usr/local/bin/k3s')} && chmod 755 /usr/local/bin/k3s\n"
        f"{common.curl(ctx, g.generic('k3s-airgap-images', versions.K3S, f'k3s-airgap-images-{versions.ARCH}.tar.gz'), IMAGES_DIR + '/k3s-airgap-images.tar.gz')}\n"
        f"{common.curl(ctx, g.generic('k3s-install', versions.K3S, 'install.sh'), '/tmp/k3s-install.sh')}\n"
        f"INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_EXEC={exec_args!r} {env} sh /tmp/k3s-install.sh\n"
        "rm -f /tmp/k3s-install.sh\n"
    )


def setup(ctx: common.NodeContext, hosts: list, log=print) -> dict:
    facts = {}
    server = hosts[0]
    facts[server.alias] = common.prepare_node(server, ctx, log)
    server_ip = facts[server.alias]["ip"]
    _install(server, ctx, f"server --node-ip {server_ip} --resolv-conf /etc/outpost/resolv.conf {ctx.args}".strip(), "", log)
    # ready means: the node reports Ready and the default service account exists, so pods can be created
    server.run(
        "for i in $(seq 1 90); do "
        "k3s kubectl get nodes --no-headers 2>/dev/null | grep -q ' Ready' && k3s kubectl -n default get serviceaccount default >/dev/null 2>&1 && break; "
        "sleep 2; done; k3s kubectl get nodes")
    token = server.run("cat /var/lib/rancher/k3s/server/node-token").stdout.strip()
    log(f"  [{server.alias}] k3s server ready")

    for host in hosts[1:]:
        facts[host.alias] = common.prepare_node(host, ctx, log)
        _install(host, ctx, "agent --resolv-conf /etc/outpost/resolv.conf", f"K3S_URL=https://{server_ip}:6443 K3S_TOKEN={token}", log)
        log(f"  [{host.alias}] k3s agent joined")
    return {"nodes": facts, "server": server.alias}


def extra_checks(ctx: common.NodeContext) -> list[tuple[str, str]]:
    return [
        ("k3s installed", "k3s --version | head -1"),
        (f"k3s at pinned version {versions.K3S}", f"k3s --version | head -1 | grep -q {versions.K3S.split('+')[0]}"),
        ("registries.yaml points at gitea", f"grep -q {ctx.gitea.name} /etc/rancher/k3s/registries.yaml"),
        ("k3s service active", "systemctl is-active k3s || systemctl is-active k3s-agent"),
    ]
