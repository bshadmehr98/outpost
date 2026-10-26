"""Steps every node goes through, whatever runs on it afterwards.

  1. know the Gitea server by name              /etc/hosts
  2. trust the private CA                       system trust store + explicit paths for pip/npm/apt/docker
  3. point pip, npm, apt and Docker at Gitea    small config files from clients.py
  4. park the stock apt sources                 /etc/apt/sources.disabled/
  5. seal egress                                outpost-egress seal
  6. install CoreDNS from Gitea                 so containers can resolve the Gitea name
  7. install Docker from Gitea                  compose and swarm only

Everything after step 5 runs with the internet blocked. That is the point: if setup
succeeds, the node provably needs nothing from outside.
"""

import shlex
from dataclasses import dataclass, field

from .. import versions
from . import clients, egress, hostsfile
from .clients import GiteaInfo

DNS_UNIT = """[Unit]
Description=Outpost node DNS (CoreDNS: answers the Gitea name for containers)
After=network-online.target
Wants=network-online.target
Before=docker.service k3s.service k3s-agent.service

[Service]
ExecStart=/usr/local/bin/coredns -conf /etc/outpost/Corefile
Restart=always
RestartSec=2
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""

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


@dataclass
class NodeContext:
    gitea: GiteaInfo
    ca_pem: str
    distros: list[str] = field(default_factory=list)   # apt distros that exist in Gitea
    allow: list[str] = field(default_factory=list)     # extra reachable addresses (other nodes, user list)
    infra_name: str = ""
    args: str = ""                                     # extra flags for the runtime (k3s server, swarm init)


def curl(ctx: NodeContext, url: str, out: str) -> str:
    return f"curl --cacert {clients.PATH_CA} -fsSL {shlex.quote(url)} -o {shlex.quote(out)}"


def prepare_node(host, ctx: NodeContext, log=print) -> dict:
    log(f"==> {host.alias}: connecting and configuring the clients")
    facts = host.facts()
    g = ctx.gitea
    log(f"  [{host.alias}] {facts.get('id', '?')} {facts.get('codename', '')} {facts.get('arch', '')} ip={facts.get('ip', '?')}")
    want = versions.names()["uname"]
    if facts.get("arch") and facts["arch"] != want:
        raise RuntimeError(f"[{host.alias}] is {facts['arch']} but this platform is built for {want} (arch: {versions.ARCH} in servers.yml).")

    # 1. name resolution without DNS (rewrite in place: /etc/hosts can be a bind mount), and a unit that
    #    puts the line back at boot, because cloud-init and Docker both rewrite /etc/hosts
    host.run(f"{{ grep -v {shlex.quote(f' {g.name}$')} /etc/hosts; echo {shlex.quote(f'{g.ip} {g.name}')}; }} > /etc/hosts.outpost && cat /etc/hosts.outpost > /etc/hosts && rm /etc/hosts.outpost")
    hostsfile.install(host, g.ip, g.name)

    # 2 + 3. trust and client configuration
    node_ip = facts.get("ip") or host.ip()
    for path, content in {**clients.client_files(g, ctx.ca_pem, apt_key=None, distros=[]), **clients.host_files(g, ctx.ca_pem, node_ip)}.items():
        host.write(path, content)
    host.run(
        "if command -v update-ca-certificates >/dev/null; then "
        f"  cp {clients.PATH_CA} /usr/local/share/ca-certificates/outpost-ca.crt && update-ca-certificates >/dev/null; "
        "elif command -v update-ca-trust >/dev/null; then "
        f"  cp {clients.PATH_CA} /etc/pki/ca-trust/source/anchors/outpost-ca.crt && update-ca-trust; fi"
    )
    reach = host.run(f"curl --cacert {clients.PATH_CA} -fsS --max-time 10 {g.url}/api/v1/version", check=False)
    if not reach.ok:
        raise RuntimeError(f"[{host.alias}] cannot reach {g.url}: {reach.stderr.strip()}. Check servers.yml gitea.ip and firewalls (port 443).")
    log(f"  [{host.alias}] reaches Gitea {reach.stdout.strip()}")

    # 4. apt: fetch the repo key, keep only distros Gitea actually has, park the stock sources
    distros = list(dict.fromkeys(ctx.distros + ([facts["codename"]] if facts.get("codename") else [])))
    lines = "\n".join(
        f"if curl --cacert {clients.PATH_CA} -fsI --max-time 10 {g.apt_repo}/dists/{d}/Release >/dev/null 2>&1; then echo {shlex.quote(clients.apt_source_line(g, d))}; fi"
        for d in distros
    )
    host.run(
        f"{curl(ctx, g.apt_repo + '/repository.key', clients.PATH_APT_KEY)} || true\n"
        f"mkdir -p /etc/apt/sources.disabled /etc/apt/sources.list.d\n"
        f"for f in /etc/apt/sources.list.d/*; do [ -e \"$f\" ] && [ \"$(basename $f)\" != outpost.list ] && mv \"$f\" /etc/apt/sources.disabled/; done; true\n"
        f"if [ -s /etc/apt/sources.list ]; then mv /etc/apt/sources.list /etc/apt/sources.disabled/sources.list; fi\n"
        f"{{ {lines}\n}} > /etc/apt/sources.list.d/outpost.list\n"
    )

    # 5. seal
    log(f"==> {host.alias}: switching the internet off")
    egress.install(host, g.ip, ctx.allow)
    log(f"  [{host.alias}] egress: {egress.seal(host, f'setup {ctx.infra_name}'.strip())}")

    # 6. DNS for containers, from Gitea, with the internet already blocked
    log(f"==> {host.alias}: installing node DNS from Gitea")
    install_dns(host, ctx, log)
    return facts


def install_dns(host, ctx: NodeContext, log=print) -> None:
    g = ctx.gitea
    url = g.generic("coredns", versions.COREDNS, f"coredns_{versions.COREDNS}_linux_{versions.ARCH}.tgz")
    if versions.COREDNS not in host.run("/usr/local/bin/coredns -version 2>/dev/null || true").stdout:
        host.run(
            f"{curl(ctx, url, '/tmp/coredns.tgz')}\n"
            "tar -xzf /tmp/coredns.tgz -C /usr/local/bin coredns && chmod 755 /usr/local/bin/coredns && rm -f /tmp/coredns.tgz\n"
        )
    host.write("/etc/systemd/system/outpost-dns.service", DNS_UNIT)
    host.run("systemctl daemon-reload && systemctl enable outpost-dns >/dev/null 2>&1; systemctl restart outpost-dns && sleep 1 && systemctl is-active outpost-dns")
    log(f"  [{host.alias}] node dns (coredns {versions.COREDNS}) answers {g.name} for containers")


def install_docker(host, ctx: NodeContext, log=print) -> None:
    g = ctx.gitea
    docker_url = g.generic("docker", versions.DOCKER, f"docker-{versions.DOCKER}.tgz")
    compose_url = g.generic("docker-compose", versions.COMPOSE, "docker-compose")
    buildx_url = g.generic("docker-buildx", versions.BUILDX, "docker-buildx")
    have = host.run("docker --version 2>/dev/null || true").stdout
    if versions.DOCKER in have:
        log(f"  [{host.alias}] docker {versions.DOCKER} already installed")
    else:
        log(f"==> {host.alias}: installing Docker {versions.DOCKER} from Gitea")
        if have.strip():
            log(f"  [{host.alias}] replacing {have.strip()}; running containers restart with the daemon")
        host.run(
            f"{curl(ctx, docker_url, '/tmp/docker.tgz')}\n"
            "tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 && rm -f /tmp/docker.tgz\n"
            "groupadd -f docker\n"
            "if [ -n \"${SUDO_USER:-}\" ]; then usermod -aG docker \"$SUDO_USER\"; fi\n"
        )
        host.write("/etc/systemd/system/docker.service", DOCKER_UNIT)
        # restart, not `enable --now`: an already running daemon would keep the old binary
        host.run("systemctl daemon-reload && systemctl enable docker >/dev/null 2>&1; systemctl restart docker")
    host.run(
        "mkdir -p /usr/local/lib/docker/cli-plugins\n"
        f"if ! docker compose version 2>/dev/null | grep -q {versions.COMPOSE}; then "
        f"{curl(ctx, compose_url, '/usr/local/lib/docker/cli-plugins/docker-compose')} && chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose; fi\n"
        f"if ! docker buildx version 2>/dev/null | grep -q {versions.BUILDX}; then "
        f"{curl(ctx, buildx_url, '/usr/local/lib/docker/cli-plugins/docker-buildx')} && chmod 755 /usr/local/lib/docker/cli-plugins/docker-buildx; fi\n"
        "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done\n"
        "docker info --format '{{.ServerVersion}}' >/dev/null"
    )
    log(f"  [{host.alias}] docker ready, compose {versions.COMPOSE}, buildx {versions.BUILDX}")
