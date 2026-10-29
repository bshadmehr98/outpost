"""Docker from Gitea, for compose and swarm nodes: the static binaries, the compose and buildx plugins.

Docker's daemon gets no proxy settings and no registry mirrors (client_config): images are named by
their Gitea name, so a bare Docker Hub pull fails at the seal.
"""

from outpost import versions
from outpost.client_config import PATH_CA
from outpost.infra.context import NodeContext
from outpost.infra.node.download import download_from_gitea
from outpost.ssh import Host

DOCKER_UNIT_PATH = "/etc/systemd/system/docker.service"
CLI_PLUGINS = "/usr/local/lib/docker/cli-plugins"

DOCKER_UNIT = """[Unit]
Description=Docker (static binaries installed by Outpost)
After=network-online.target outpost-egress.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""

# restart, not `enable --now`: an already running daemon would keep the old binary
RESTART_DOCKER = "systemctl daemon-reload && systemctl enable docker >/dev/null 2>&1; systemctl restart docker"

# the ssh user that ran `sudo` may use docker without sudo
UNPACK_DOCKER = (
    "tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 && rm -f /tmp/docker.tgz\n"
    "groupadd -f docker\n"
    'if [ -n "${SUDO_USER:-}" ]; then usermod -aG docker "$SUDO_USER"; fi\n'
)

WAIT_FOR_DOCKER = (
    "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done\n"
    "docker info --format '{{.ServerVersion}}' >/dev/null"
)


def install_docker(host: Host, ctx: NodeContext, log=print) -> None:
    """Install Docker at the pinned version (replacing another version), then the plugins, and wait
    until the daemon answers."""
    installed = host.run("docker --version 2>/dev/null || true", raise_on_failure=True).stdout
    if versions.DOCKER in installed:
        log(f"  [{host.alias}] docker {versions.DOCKER} already installed")
    else:
        _install_docker_binaries(host, ctx, installed.strip(), log)
    host.run(_install_plugins_and_wait(ctx), raise_on_failure=True)
    log(f"  [{host.alias}] docker ready, compose {versions.COMPOSE}, buildx {versions.BUILDX}")


def registry_verification(ctx: NodeContext) -> str:
    """A command that passes when Docker has the CA for Gitea's registry and the registry answers /v2/
    (200, or 401 asking for a token). It pulls nothing, so it holds on a sealed node with an empty registry."""
    g = ctx.gitea
    return (
        f"test -s /etc/docker/certs.d/{g.name}/ca.crt && "
        f"code=$(curl --cacert {PATH_CA} -sS --max-time 10 -o /dev/null -w '%{{http_code}}' {g.url}/v2/) && "
        f'{{ [ "$code" = 200 ] || [ "$code" = 401 ]; }} && echo "/v2/ -> $code"'
    )


def _install_docker_binaries(host: Host, ctx: NodeContext, replacing: str, log) -> None:
    log(f"==> {host.alias}: installing Docker {versions.DOCKER} from Gitea")
    if replacing:
        log(f"  [{host.alias}] replacing {replacing}; running containers restart with the daemon")
    url = ctx.gitea.generic("docker", versions.DOCKER, f"docker-{versions.DOCKER}.tgz")
    host.run(f"{download_from_gitea(url, '/tmp/docker.tgz')}\n{UNPACK_DOCKER}", raise_on_failure=True)
    host.write(DOCKER_UNIT_PATH, DOCKER_UNIT)
    host.run(RESTART_DOCKER, raise_on_failure=True)


def _install_plugins_and_wait(ctx: NodeContext) -> str:
    """Shell: the compose and buildx plugins, each unless it is at the pinned version, then wait for the daemon."""
    g = ctx.gitea
    plugins = [
        ("compose", versions.COMPOSE, g.generic("docker-compose", versions.COMPOSE, "docker-compose")),
        ("buildx", versions.BUILDX, g.generic("docker-buildx", versions.BUILDX, "docker-buildx")),
    ]
    install_unless_pinned = "".join(
        f"if ! docker {name} version 2>/dev/null | grep -q {version}; then "
        f"{download_from_gitea(url, f'{CLI_PLUGINS}/docker-{name}')} && chmod 755 {CLI_PLUGINS}/docker-{name}; fi\n"
        for name, version, url in plugins
    )
    return f"mkdir -p {CLI_PLUGINS}\n{install_unless_pinned}{WAIT_FOR_DOCKER}"
