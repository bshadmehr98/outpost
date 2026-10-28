"""k3s: a small Kubernetes. The first node is the server, the rest are agents.

k3s ships an "airgap images" tarball with everything the cluster itself needs; we place it
where k3s looks and tell containerd to trust Gitea's certificate. Application images are
referenced by their Gitea name (`gitea.internal/pkgs/library/alpine:3.20`); there is no
`docker.io` mirror, so a bare Docker Hub name fails at once on a sealed node.

The egress proxy never touches the k3s unit: proxy variables there would make containerd pull images
through the proxy. Pods opt in instead, from a ConfigMap `outpost-proxy` in the `default` namespace:

    envFrom:
      - configMapRef: {name: outpost-proxy, optional: true}
"""

import json
import shlex

from outpost import versions
from outpost.client_config import PATH_CA, PATH_NODE_RESOLV_CONF
from outpost.endpoints import ProxyEndpoint
from outpost.infra import node
from outpost.infra.context import NodeContext
from outpost.ssh import Host

CONFIGMAP = "outpost-proxy"
IMAGES_DIR = "/var/lib/rancher/k3s/agent/images"
REGISTRIES_PATH = "/etc/rancher/k3s/registries.yaml"
INSTALLER = "/tmp/k3s-install.sh"

# ready means: the node reports Ready and the default service account exists, so pods can be created
WAIT_UNTIL_READY = (
    "for i in $(seq 1 90); do "
    "k3s kubectl get nodes --no-headers 2>/dev/null | grep -q ' Ready' && k3s kubectl -n default get serviceaccount default >/dev/null 2>&1 && break; "
    "sleep 2; done; k3s kubectl get nodes"
)


def setup(ctx: NodeContext, hosts: list[Host], log=print) -> dict:
    """Prepare the first node and make it the k3s server, then prepare each other node and join it as an agent.

    Returns:
        {"nodes": {alias: facts}, "server": alias}
    """
    facts = {}
    server, agents = hosts[0], hosts[1:]
    facts[server.alias] = node.prepare_node(server, ctx, log)
    server_ip = facts[server.alias]["ip"]
    server_args = f"server --node-ip {server_ip} --resolv-conf {PATH_NODE_RESOLV_CONF} {ctx.args}".strip()
    _install(server, ctx, server_args, {}, log)
    server.run(WAIT_UNTIL_READY, raise_on_failure=True)
    join_token = server.run("cat /var/lib/rancher/k3s/server/node-token", raise_on_failure=True).stdout.strip()
    log(f"  [{server.alias}] k3s server ready")
    apply_configmap(server, ctx.proxy, log)

    for host in agents:
        facts[host.alias] = node.prepare_node(host, ctx, log)
        join = {"K3S_URL": f"https://{server_ip}:6443", "K3S_TOKEN": join_token}
        _install(host, ctx, f"agent --resolv-conf {PATH_NODE_RESOLV_CONF}", join, log)
        log(f"  [{host.alias}] k3s agent joined")
    return {"nodes": facts, "server": server.alias}


def verifications(ctx: NodeContext) -> list[tuple[str, str]]:
    """What a k3s node runs on top of the node verifications."""
    return [
        ("k3s installed", "k3s --version | head -1"),
        (f"k3s at pinned version {versions.K3S}", f"k3s --version | head -1 | grep -q {versions.K3S.split('+')[0]}"),
        ("registries.yaml points at gitea", f"grep -q {ctx.gitea.name} {REGISTRIES_PATH}"),
        ("k3s service active", "systemctl is-active k3s || systemctl is-active k3s-agent"),
    ]


def registries_yaml(ctx: NodeContext) -> str:
    """/etc/rancher/k3s/registries.yaml: containerd pulls Gitea's images from Gitea, trusting the CA."""
    g = ctx.gitea
    return (
        "mirrors:\n"
        f'  "{g.name}":\n'
        f'    endpoint:\n      - "{g.url}"\n'
        "configs:\n"
        f'  "{g.name}":\n'
        f"    tls:\n      ca_file: {PATH_CA}\n"
    )


def configmap_yaml(proxy: ProxyEndpoint) -> str:
    """The proxy variables as a ConfigMap. JSON strings are valid YAML scalars, so values are quoted safely."""
    data = {**proxy.env(), "OUTPOST_PROXY_ALLOWED": ",".join(proxy.allowed)}
    return (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
        f"  name: {CONFIGMAP}\n  namespace: default\n  labels:\n    app.kubernetes.io/managed-by: outpost\n"
        "data:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in data.items())
    )


def apply_configmap(server: Host, proxy: ProxyEndpoint | None, log=print) -> None:
    """Create or update the proxy's ConfigMap; without a proxy, remove it.

    Safe to run again: `kubectl apply` of the same ConfigMap changes nothing.
    """
    if proxy:
        server.run(
            f"cat <<'OUTPOST_EOF' | k3s kubectl apply -f -\n{configmap_yaml(proxy)}OUTPOST_EOF\n", raise_on_failure=True
        )
        log(f"  [{server.alias}] ConfigMap default/{CONFIGMAP}: pods opt in with envFrom configMapRef {CONFIGMAP}")
    else:
        server.run(
            f"k3s kubectl -n default delete configmap {CONFIGMAP} --ignore-not-found >/dev/null", raise_on_failure=True
        )


def _install(host: Host, ctx: NodeContext, exec_args: str, env: dict[str, str], log) -> None:
    """Install k3s at the pinned version, unless it is there already.

    Args:
        exec_args: INSTALL_K3S_EXEC, the k3s command line (server or agent, and its flags).
        env: more variables for the installer, such as K3S_URL and K3S_TOKEN for an agent.
    """
    installed = host.run("k3s --version 2>/dev/null | head -1 || true", raise_on_failure=True).stdout
    if versions.K3S.split("+")[0] in installed:
        log(f"  [{host.alias}] k3s {versions.K3S} already installed")
        return
    log(f"==> {host.alias}: installing k3s {versions.K3S} from Gitea")
    host.write(REGISTRIES_PATH, registries_yaml(ctx))
    host.run(_install_script(ctx, exec_args, env), raise_on_failure=True)


def _install_script(ctx: NodeContext, exec_args: str, env: dict[str, str]) -> str:
    """Shell: download k3s, its airgap images and its installer from Gitea, and run the installer offline.

    Each value is quoted with shlex.quote: `args:` in servers.yml may hold quotes, `$` or backticks.
    """
    g = ctx.gitea
    binary = g.generic("k3s", versions.K3S, versions.arch_names()["k3s"])
    images = g.generic("k3s-airgap-images", versions.K3S, f"k3s-airgap-images-{versions.ARCH}.tar.gz")
    installer = g.generic("k3s-install", versions.K3S, "install.sh")
    installer_env = " ".join(
        [
            "INSTALL_K3S_SKIP_DOWNLOAD=true",  # use the binary we put in /usr/local/bin
            f"INSTALL_K3S_EXEC={shlex.quote(exec_args)}",
            *[f"{name}={shlex.quote(value)}" for name, value in env.items()],
        ]
    )
    return (
        f"mkdir -p {IMAGES_DIR}\n"
        f"{node.download_from_gitea(binary, '/usr/local/bin/k3s')} && chmod 755 /usr/local/bin/k3s\n"
        f"{node.download_from_gitea(images, IMAGES_DIR + '/k3s-airgap-images.tar.gz')}\n"
        f"{node.download_from_gitea(installer, INSTALLER)}\n"
        f"{installer_env} sh {INSTALLER}\n"
        f"rm -f {INSTALLER}\n"
    )
