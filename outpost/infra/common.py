"""Steps every node goes through, whatever runs on it afterwards.

  1. know the Gitea server by name              /etc/hosts
  2. trust the private CA                       system trust store + explicit paths for pip/npm/apt/docker
  3. point pip, npm, apt and Docker at Gitea    small config files from clients.py
  4. OS packages from Gitea, by family          apt (Debian/Ubuntu), apk (Alpine) or dnf (RHEL family):
                                                fetch the key, park the stock sources, write the Gitea one
  5. seal egress                                outpost-egress seal
  6. install CoreDNS from Gitea                 so containers can resolve the Gitea name
  7. install Docker from Gitea                  compose and swarm only

Everything after step 5 runs with the internet blocked. That is the point: if setup
succeeds, the node provably needs nothing from outside.

With an egress proxy in servers.yml, steps 1 and 3 also carry its settings (apply_proxy): the proxy's
name in /etc/hosts when it is not on the Gitea host, /etc/outpost/proxy.env, the profile.d exports,
/etc/environment and the Docker client config. Step 5 then allows the proxy's address too.
"""

import shlex

from .. import versions
from ..fetch import rpm as rpmmod
from . import clients, egress, hostsfile
from .context import NodeContext

RUNNER_HOME = "/var/lib/outpost-runner"  # runner.HOME; Actions jobs read $HOME/.docker/config.json there

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


def curl(ctx: NodeContext, url: str, out: str) -> str:
    return f"curl --cacert {clients.PATH_CA} -fsSL {shlex.quote(url)} -o {shlex.quote(out)}"


def os_family(facts: dict) -> str:
    """apt, apk or rpm, from /etc/os-release's ID and ID_LIKE. Anything unrecognised is treated as apt,
    which is what every node was assumed to be before Alpine and the RHEL family were supported."""
    ids = f"{facts.get('id', '')} {facts.get('id_like', '')}".lower().split()
    if "alpine" in ids:
        return "apk"
    if any(i in ("rhel", "fedora", "centos", "rocky", "almalinux") for i in ids):
        return "rpm"
    return "apt"


def apk_branch_of(facts: dict) -> str | None:
    """An Alpine host's own branch: VERSION_ID 3.20.3 -> v3.20."""
    parts = str(facts.get("version_id", "")).split(".")
    return f"v{parts[0]}.{parts[1]}" if len(parts) >= 2 and all(x.isdigit() for x in parts[:2]) else None


def registry_check(ctx: NodeContext) -> str:
    """Docker has the CA for Gitea's registry and the registry answers /v2/ (200, or 401 asking for a token).
    Pulls nothing, so it holds on a sealed node with an empty registry."""
    g = ctx.gitea
    return (
        f"test -s /etc/docker/certs.d/{g.name}/ca.crt && "
        f"code=$(curl --cacert {clients.PATH_CA} -sS --max-time 10 -o /dev/null -w '%{{http_code}}' {g.url}/v2/) && "
        f'{{ [ "$code" = 200 ] || [ "$code" = 401 ]; }} && echo "/v2/ -> $code"'
    )


def prepare_node(host, ctx: NodeContext, log=print) -> dict:
    log(f"==> {host.alias}: connecting and configuring the clients")
    facts = host.facts()
    g = ctx.gitea
    log(
        f"  [{host.alias}] {facts.get('id', '?')} {facts.get('codename', '')} {facts.get('arch', '')} ip={facts.get('ip', '?')}"
    )
    want = versions.names()["uname"]
    if facts.get("arch") and facts["arch"] != want:
        raise RuntimeError(
            f"[{host.alias}] is {facts['arch']} but this platform is built for {want} (arch: {versions.ARCH} in servers.yml)."
        )

    # 1. name resolution without DNS (rewrite in place: /etc/hosts can be a bind mount), and a unit that
    #    puts the line back at boot, because cloud-init and Docker both rewrite /etc/hosts
    _write_hosts(host, ctx)

    # 2 + 3. trust and client configuration
    node_ip = facts.get("ip") or host.ip()
    for path, content in {
        **clients.client_files(g, ctx.ca_pem, apt_key=None, distros=[], proxy=ctx.proxy),
        **clients.host_files(g, ctx.ca_pem, node_ip, proxy=ctx.proxy),
    }.items():
        host.write(path, content)
    _proxy_settings(host, ctx, log)
    # Debian and Alpine both ship update-ca-certificates reading /usr/local/share/ca-certificates (Alpine's
    # comes with the ca-certificates package); the RHEL family has update-ca-trust and its anchors folder.
    host.run(
        "if command -v update-ca-certificates >/dev/null; then "
        f"  cp {clients.PATH_CA} /usr/local/share/ca-certificates/outpost-ca.crt && update-ca-certificates >/dev/null; "
        "elif command -v update-ca-trust >/dev/null; then "
        f"  cp {clients.PATH_CA} /etc/pki/ca-trust/source/anchors/outpost-ca.crt && update-ca-trust; fi",
        raise_on_failure=True,
    )
    reach = host.run(f"curl --cacert {clients.PATH_CA} -fsS --max-time 10 {g.url}/api/v1/version")
    if not reach.ok:
        raise RuntimeError(
            f"[{host.alias}] cannot reach {g.url}: {reach.stderr.strip()}. Check servers.yml gitea.ip and firewalls (port 443)."
        )
    log(f"  [{host.alias}] reaches Gitea {reach.stdout.strip()}")

    # 4. OS packages from Gitea: fetch the repo key, keep only what Gitea actually has, park the stock sources
    family = os_family(facts)
    log(f"  [{host.alias}] os packages: {family}")
    {"apt": _configure_apt, "apk": _configure_apk, "rpm": _configure_rpm}[family](host, ctx, facts)

    # 5. seal
    log(f"==> {host.alias}: switching the internet off")
    egress.install(host, g.ip, ctx.allow, proxy_user=ctx.proxy_user_for(host.alias))
    log(f"  [{host.alias}] egress: {egress.seal(host, f'setup {ctx.infra_name}'.strip())}")

    # 6. DNS for containers, from Gitea, with the internet already blocked
    log(f"==> {host.alias}: installing node DNS from Gitea")
    install_dns(host, ctx, log)
    return facts


def _write_hosts(host, ctx: NodeContext) -> None:
    entries = ctx.hosts_entries()
    host.run(hostsfile.rewrite(entries), raise_on_failure=True)
    hostsfile.install(host, entries[0][0], entries[0][1], entries[1:])


def _proxy_settings(host, ctx: NodeContext, log=print) -> None:
    """The proxy settings that are merged into existing files rather than written whole: the Docker client
    config of root and of the Actions runner, and /etc/environment. Without a proxy only what Outpost wrote
    before is removed, and /etc/outpost/proxy.env is left as a comment, so `env_file:` can always name it."""
    g = ctx.gitea
    if not ctx.proxy:
        host.write(clients.PATH_PROXY_ENV, clients.NO_PROXY_ENV)
    for home in ("/root", RUNNER_HOME):
        path = f"{home}/.docker/config.json"
        existing = host.run(f"cat {path} 2>/dev/null || true").stdout
        updated = clients.docker_client_config(existing, ctx.proxy, g.name)
        if updated is not None:
            host.write(path, updated, mode="0600")
    host.run(clients.environment_script(ctx.proxy), raise_on_failure=True)
    if ctx.proxy:
        log(
            f"  [{host.alias}] egress proxy {ctx.proxy.url} for programs that opt in (HTTPS_PROXY), containers through the Docker client config"
        )


def apply_proxy(host, ctx: NodeContext, log=print) -> None:
    """Bring an already set-up node up to date with the egress proxy (or its removal), without redoing
    the rest of prepare_node: names, files, merged settings, and the seal when the proxy's address is new."""
    g = ctx.gitea
    _write_hosts(host, ctx)
    files = {clients.PATH_PROFILE: clients.profile_script(g, ctx.proxy)}
    if ctx.proxy:
        files[clients.PATH_PROXY_ENV] = clients.proxy_env(ctx.proxy)
    for path, content in files.items():
        host.write(path, content)
    _proxy_settings(host, ctx, log)
    if ctx.proxy and not ctx.proxy.on_gitea:
        egress.install(host, g.ip, ctx.allow, proxy_user=ctx.proxy_user_for(host.alias))
        log(f"  [{host.alias}] egress: {egress.seal(host, 'proxy setup')} (allows the proxy at {ctx.proxy.ip})")


def _configure_apt(host, ctx: NodeContext, facts: dict) -> None:
    """Debian and Ubuntu: one line in outpost.list per suite Gitea serves. The probe is a GET: Gitea routes
    the Debian registry's dists/ files for GET only, so a HEAD (curl -I) gets 405 and every suite looked
    missing (Gitea's Alpine and RPM registries do answer HEAD)."""
    g = ctx.gitea
    distros = list(dict.fromkeys(ctx.distros + ([facts["codename"]] if facts.get("codename") else [])))
    lines = "\n".join(
        f"if curl --cacert {clients.PATH_CA} -fsS -o /dev/null --max-time 10 {g.apt_repo}/dists/{d}/Release >/dev/null 2>&1; then echo {shlex.quote(clients.apt_source_line(g, d))}; fi"
        for d in distros
    )
    host.run(
        f"{curl(ctx, g.apt_repo + '/repository.key', clients.PATH_APT_KEY)} || true\n"
        f"mkdir -p /etc/apt/sources.disabled /etc/apt/sources.list.d\n"
        f'for f in /etc/apt/sources.list.d/*; do [ -e "$f" ] && [ "$(basename $f)" != outpost.list ] && mv "$f" /etc/apt/sources.disabled/; done; true\n'
        f"if [ -s /etc/apt/sources.list ]; then mv /etc/apt/sources.list /etc/apt/sources.disabled/sources.list; fi\n"
        f"{{ {lines}\n}} > /etc/apt/sources.list.d/outpost.list\n",
        raise_on_failure=True,
    )


def _configure_apk(host, ctx: NodeContext, facts: dict) -> None:
    """Alpine: the key goes to /etc/apk/keys under the name Gitea sends (curl -J keeps it), the stock
    /etc/apk/repositories is parked as repositories.disabled, and only branches Gitea serves are written."""
    g = ctx.gitea
    arch = versions.names()["uname"]
    own = apk_branch_of(facts)
    branches = list(dict.fromkeys(ctx.apk_branches + ([own] if own else [])))
    lines = "\n".join(
        f"if curl --cacert {clients.PATH_CA} -fsI --max-time 10 {g.apk_repo}/{b}/main/{arch}/APKINDEX.tar.gz >/dev/null 2>&1; then echo {shlex.quote(clients.apk_repository_line(g, b))}; fi"
        for b in branches
    )
    host.run(
        f"mkdir -p {clients.PATH_APK_KEYS}\n"
        f"find {clients.PATH_APK_KEYS} -name {shlex.quote(g.org + '@*.rsa.pub')} -delete\n"
        # -J saves under the name in Content-Disposition, the one Gitea signed with; `key` means the header was missing
        f"(cd {clients.PATH_APK_KEYS} && curl --cacert {clients.PATH_CA} -fsSL -J -O {shlex.quote(g.apk_repo + '/key')} && "
        f"{{ [ ! -f key ] || mv key {shlex.quote(g.org + '.rsa.pub')}; }}) || true\n"
        f"if [ -e {clients.PATH_APK_REPOS} ] && ! grep -q {shlex.quote(g.apk_repo)} {clients.PATH_APK_REPOS}; then mv {clients.PATH_APK_REPOS} {clients.PATH_APK_REPOS}.disabled; fi\n"
        f"{{ {lines}\n}} > {clients.PATH_APK_REPOS}\n",
        raise_on_failure=True,
    )


def _configure_rpm(host, ctx: NodeContext, facts: dict) -> None:
    """RHEL family: Gitea's key to /etc/outpost/gitea-rpm.asc, the stock *.repo files parked in
    /etc/yum.repos.d.disabled/, and outpost.repo with one section per distro (Gitea group) that exists."""
    g = ctx.gitea
    own = rpmmod.distro_for_host(facts.get("id", ""), facts.get("version_id", ""))
    distros = list(dict.fromkeys(ctx.rpm_distros + ([own] if own else [])))
    lines = "\n".join(
        f"if curl --cacert {clients.PATH_CA} -fsI --max-time 10 {g.rpm_repo}/{d}/repodata/repomd.xml >/dev/null 2>&1; then printf '%s\\n' {shlex.quote(clients.rpm_repo_section(g, d))}; fi"
        for d in distros
    )
    host.run(
        f"{curl(ctx, g.rpm_repo + '/repository.key', clients.PATH_RPM_KEY)} || true\n"
        f"mkdir -p /etc/yum.repos.d.disabled /etc/yum.repos.d\n"
        f'for f in /etc/yum.repos.d/*.repo; do [ -e "$f" ] && [ "$(basename $f)" != outpost.repo ] && mv "$f" /etc/yum.repos.d.disabled/; done; true\n'
        f"{{ {lines}\n}} > {clients.PATH_RPM_REPO}\n",
        raise_on_failure=True,
    )


def install_dns(host, ctx: NodeContext, log=print) -> None:
    g = ctx.gitea
    url = g.generic("coredns", versions.COREDNS, f"coredns_{versions.COREDNS}_linux_{versions.ARCH}.tgz")
    if (
        versions.COREDNS
        not in host.run("/usr/local/bin/coredns -version 2>/dev/null || true", raise_on_failure=True).stdout
    ):
        host.run(
            f"{curl(ctx, url, '/tmp/coredns.tgz')}\n"
            "tar -xzf /tmp/coredns.tgz -C /usr/local/bin coredns && chmod 755 /usr/local/bin/coredns && rm -f /tmp/coredns.tgz\n",
            raise_on_failure=True,
        )
    host.write("/etc/systemd/system/outpost-dns.service", DNS_UNIT)
    host.run(
        "systemctl daemon-reload && systemctl enable outpost-dns >/dev/null 2>&1; systemctl restart outpost-dns && sleep 1 && systemctl is-active outpost-dns",
        raise_on_failure=True,
    )
    log(f"  [{host.alias}] node dns (coredns {versions.COREDNS}) answers {g.name} for containers")


def install_docker(host, ctx: NodeContext, log=print) -> None:
    g = ctx.gitea
    docker_url = g.generic("docker", versions.DOCKER, f"docker-{versions.DOCKER}.tgz")
    compose_url = g.generic("docker-compose", versions.COMPOSE, "docker-compose")
    buildx_url = g.generic("docker-buildx", versions.BUILDX, "docker-buildx")
    have = host.run("docker --version 2>/dev/null || true", raise_on_failure=True).stdout
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
            'if [ -n "${SUDO_USER:-}" ]; then usermod -aG docker "$SUDO_USER"; fi\n',
            raise_on_failure=True,
        )
        host.write("/etc/systemd/system/docker.service", DOCKER_UNIT)
        # restart, not `enable --now`: an already running daemon would keep the old binary
        host.run(
            "systemctl daemon-reload && systemctl enable docker >/dev/null 2>&1; systemctl restart docker",
            raise_on_failure=True,
        )
    host.run(
        "mkdir -p /usr/local/lib/docker/cli-plugins\n"
        f"if ! docker compose version 2>/dev/null | grep -q {versions.COMPOSE}; then "
        f"{curl(ctx, compose_url, '/usr/local/lib/docker/cli-plugins/docker-compose')} && chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose; fi\n"
        f"if ! docker buildx version 2>/dev/null | grep -q {versions.BUILDX}; then "
        f"{curl(ctx, buildx_url, '/usr/local/lib/docker/cli-plugins/docker-buildx')} && chmod 755 /usr/local/lib/docker/cli-plugins/docker-buildx; fi\n"
        "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done\n"
        "docker info --format '{{.ServerVersion}}' >/dev/null",
        raise_on_failure=True,
    )
    log(f"  [{host.alias}] docker ready, compose {versions.COMPOSE}, buildx {versions.BUILDX}")
