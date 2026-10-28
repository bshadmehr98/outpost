"""Install and seal the egress proxy on one server, in six steps logged as `==> [n/6]`."""

import re

from outpost import client_config, egress, versions
from outpost.client_config import PATH_CA
from outpost.config import ProxyConfig
from outpost.distros import distro_of_host, os_family
from outpost.infra import node
from outpost.infra.context import NodeContext
from outpost.infra.proxy.allow_list import allow_file
from outpost.infra.proxy.diagnose import access_report, diagnose_script
from outpost.infra.proxy.tinyproxy import (
    ALLOW_PATH,
    CONF_PATH,
    INSTALL,
    LOG_PATH,
    LOG_PREP,
    LOGROTATE,
    LOGROTATE_PATH,
    NO_PACKAGE_MARKER,
    OLD_PATHS,
    REGISTRY,
    STOCK_UNIT,
    UNIT_PATH,
    USER,
    conf,
    unit,
)
from outpost.needs import PROXY_PACKAGE
from outpost.ssh import Host

DEFAULT_VERSION = "1.11.1"  # assumed when `tinyproxy -v` says nothing we can read

MAKE_USER_AND_FOLDERS = (
    f"getent group {USER} >/dev/null || groupadd --system {USER}\n"
    f"id -u {USER} >/dev/null 2>&1 || useradd --system --gid {USER} --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin {USER}\n"
    f"mkdir -p /etc/tinyproxy /usr/share/tinyproxy && chmod 0755 /etc/tinyproxy /usr/share/tinyproxy && {LOG_PREP}\n"
    f"rm -f {' '.join(OLD_PATHS)}\n"
)

USER_CAN_READ_ITS_FILES = (
    f"runuser -u {USER} -- test -r {CONF_PATH} && runuser -u {USER} -- test -r {ALLOW_PATH} && "
    f"runuser -u {USER} -- test -w {LOG_PATH}"
)

START_PROXY = (
    "systemctl daemon-reload && systemctl enable outpost-proxy >/dev/null 2>&1; "
    "systemctl restart outpost-proxy && sleep 1 && systemctl is-active outpost-proxy"
)

# Prints the binary's path and its version line, such as "/usr/bin/tinyproxy tinyproxy 1.11.1".
FIND_BINARY_AND_VERSION = (
    f"systemctl disable --now {STOCK_UNIT} >/dev/null 2>&1 || true\n"
    f"command -v {PROXY_PACKAGE} || ls /usr/sbin/{PROXY_PACKAGE} /usr/bin/{PROXY_PACKAGE} 2>/dev/null | head -1 || true\n"
    f"{PROXY_PACKAGE} -v 2>&1 | head -1 || true"
)


def set_up_on_host(
    host: Host, ctx: NodeContext, cfg: ProxyConfig, *, egress_allow: list[str], client_allow: list[str], log=print
) -> dict:
    """Install tinyproxy on host, run it as its own user, and seal host with the one exception for that user.

    ctx.proxy says where the proxy is. On the Gitea server nothing of a node is prepared (it already
    resolves its own name, and reaches Gitea on itself); elsewhere the server is prepared like a node
    first, so it trusts the CA, resolves Gitea and gets packages from it.

    Args:
        cfg: servers.yml's egress_proxy.
        egress_allow: what the sealed proxy host may reach besides Gitea and the private networks.
        client_allow: more addresses that may use the proxy.

    Returns:
        What state/proxy.json records: host, ip, port, name, url, on_gitea, allow, tinyproxy, family, distro.

    Raises:
        RuntimeError: the server runs Alpine or is of another CPU type, Gitea has no tinyproxy for its
            distro, or tinyproxy does not start; the message says what the server saw.
    """
    p = ctx.proxy
    if not p:
        raise RuntimeError("the proxy setup needs ctx.proxy")
    log(f"==> [1/6] connecting to {host.alias}")
    facts, family, distro = connect_and_check_host(host, ctx, log)

    log(f"==> [2/6] package sources: Gitea's {REGISTRY[family][0]} registry")
    point_packages_at_gitea(host, ctx, facts, family, log)

    log(f"==> [3/6] {PROXY_PACKAGE} from Gitea")
    binary, version = install_tinyproxy(host, ctx, family, distro, log)

    log(f"==> [4/6] user {USER}, configuration, allow list")
    write_user_and_config(host, ctx, cfg, client_allow, version, log)

    log("==> [5/6] service outpost-proxy")
    start_service(host, binary)

    log("==> [6/6] sealing, with the one exception for the proxy's own user")
    seal_with_exception(host, ctx, egress_allow, log)
    return {
        "host": host.alias,
        "ip": p.ip,
        "port": cfg.port,
        "name": p.name,
        "url": p.url,
        "on_gitea": p.on_gitea,
        "allow": list(cfg.allow),
        "tinyproxy": version,
        "family": family,
        "distro": distro,
    }


# -- the six steps --------------------------------------------------------------------------


def connect_and_check_host(host: Host, ctx: NodeContext, log=print) -> tuple[dict, str, str]:
    """Step 1: ask the server what it is. Returns its facts, its family (apt or rpm) and its distro.

    Raises:
        RuntimeError: the server runs Alpine, or is of another CPU type.
    """
    facts = host.facts()
    family = os_family(facts)
    if family == "apk":
        raise RuntimeError(
            f"[{host.alias}] runs Alpine, which Outpost does not support as an egress proxy host. "
            "Put the proxy on a Debian, Ubuntu or RHEL-family host (egress_proxy.host in servers.yml)."
        )
    versions.check_server_arch(host.alias, facts)
    distro = distro_of_host(facts) or facts.get("id", "?")
    p = ctx.proxy
    where = "on the Gitea host" if p and p.on_gitea else "its own host"
    log(f"  [{host.alias}] {facts.get('id', '?')} {distro}, proxy {p.url if p else '?'} ({where})")
    return facts, family, distro


def point_packages_at_gitea(host: Host, ctx: NodeContext, facts: dict, family: str, log=print) -> None:
    """Step 2: apt or dnf installs from Gitea. On the Gitea server, only what apt or dnf needs (it is no
    node); on another server, the whole node preparation, which also seals it."""
    if ctx.proxy and ctx.proxy.on_gitea:
        host.write(PATH_CA, ctx.ca_pem)
        if family == "apt":
            host.write("/etc/apt/apt.conf.d/99outpost", client_config.apt_trusts_the_ca())
        configure_sources = {"apt": node.configure_apt_sources, "rpm": node.configure_rpm_sources}[family]
        configure_sources(host, ctx, facts)
    else:
        node.prepare_node(host, ctx, log)


def install_tinyproxy(host: Host, ctx: NodeContext, family: str, distro: str, log=print) -> tuple[str, str]:
    """Step 3: the distro's tinyproxy package from Gitea, with the stock unit masked so it never starts.

    Returns:
        The binary's path and its version ("" when unknown).

    Raises:
        RuntimeError: Gitea has no tinyproxy for the distro (with what the package manager saw), or the
            install failed.
    """
    out = host.run(f"systemctl mask {STOCK_UNIT} >/dev/null 2>&1 || true\n" + INSTALL[family], timeout=900)
    if NO_PACKAGE_MARKER in out.stdout:
        registry, kind = REGISTRY[family]
        seen = host.run(diagnose_script(family, ctx.gitea, distro), timeout=300)
        detail = (seen.stdout + seen.stderr).strip()[-3000:]
        raise RuntimeError(
            f"Gitea's {registry} registry has no {PROXY_PACKAGE} for {distro}. Run `outpost fetch --only {kind}` and "
            f"`outpost upload` (with egress_proxy in servers.yml, fetch adds {PROXY_PACKAGE} for {distro} by itself), "
            f"then `outpost proxy setup` again.\nWhat [{host.alias}] sees:\n{detail}"
        )
    if not out.ok:
        raise RuntimeError(
            f"[{host.alias}] installing {PROXY_PACKAGE} from Gitea failed: {(out.stderr or out.stdout).strip()[-400:]}"
        )
    found = host.run(FIND_BINARY_AND_VERSION, raise_on_failure=True).stdout.split()
    binary = next((word for word in found if word.startswith("/")), f"/usr/bin/{PROXY_PACKAGE}")
    version = next((word for word in reversed(found) if re.fullmatch(r"\d+\.\d+(\.\d+)?\S*", word)), "")
    log(f"  [{host.alias}] {PROXY_PACKAGE} {version or '(version unknown)'} at {binary}, stock {STOCK_UNIT} masked")
    return binary, version


def write_user_and_config(
    host: Host, ctx: NodeContext, cfg: ProxyConfig, client_allow: list[str], version: str, log=print
) -> None:
    """Step 4: the proxy's system user, its configuration, the allow list and logrotate.

    Raises:
        RuntimeError: the user cannot read its configuration or write its log, with every folder on the way.
    """
    p = ctx.proxy
    assert p is not None  # set_up_on_host checked it
    host.run(MAKE_USER_AND_FOLDERS, raise_on_failure=True)
    host.write(CONF_PATH, conf(p.ip, cfg.port, client_allow, version or DEFAULT_VERSION))
    host.write(ALLOW_PATH, allow_file(list(cfg.allow), ctx.gitea.name))
    host.write(LOGROTATE_PATH, LOGROTATE)
    if not host.run(USER_CAN_READ_ITS_FILES).ok:
        raise RuntimeError(
            f"[{host.alias}] {USER} cannot read its configuration or write its log:\n{access_report(host)}"
        )
    log(f"  [{host.alias}] allows {', '.join(cfg.allow)} (and {ctx.gitea.name}); everything else gets 403")


def start_service(host: Host, binary: str) -> None:
    """Step 5: write outpost-proxy.service and (re)start it.

    Raises:
        RuntimeError: it did not start, with its journal and the access report.
    """
    host.write(UNIT_PATH, unit(binary))
    if not host.run(START_PROXY).ok:
        journal = host.run("journalctl -u outpost-proxy -n 15 --no-pager 2>&1 || true").stdout.strip()
        raise RuntimeError(
            f"[{host.alias}] outpost-proxy did not start:\n{journal[-800:]}\n{access_report(host, binary)}"
        )


def seal_with_exception(host: Host, ctx: NodeContext, egress_allow: list[str], log=print) -> None:
    """Step 6: the seal, with the one exception: the proxy's user may open TCP 443 and 80."""
    egress.install_seal(host, ctx.gitea.ip, egress_allow, proxy_user=USER)
    log(
        f"  [{host.alias}] egress: {egress.seal_host(host, 'proxy setup')}; {USER} may open TCP 443 and 80, nothing else changed"
    )
