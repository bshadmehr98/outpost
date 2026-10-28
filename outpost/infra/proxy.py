"""The egress proxy: named exceptions to the seal, through one logged tinyproxy.

The seal (egress.py) is address-based: iptables cannot match host names, and the addresses behind a
CDN change. So the few third-party services sealed workloads may call (api.twilio.com, an email API)
are reached through one outbound forward proxy that checks the *name* a client asks for:

    container --HTTPS_PROXY--> tinyproxy on a private address --only allowed names--> the internet
                   CONNECT api.twilio.com:443                  TLS stays end to end

  /etc/tinyproxy/tinyproxy.conf        tinyproxy configuration (below), in place of the package's
  /usr/share/tinyproxy/outpost.allow   one anchored extended regex per allowed name
  /var/log/tinyproxy/tinyproxy.log     every CONNECT with its host name, and every refusal
  outpost-proxy.service                tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf as user outpost-proxy
  outpost-egress                       the seal, plus the one exception: user outpost-proxy may open
                                       TCP 443 and 80 (egress.py, chain OUTPOST-OUT, OUTPUT only)

tinyproxy comes from the distro's own package, through Gitea (apt on Debian and Ubuntu, dnf on the RHEL
family; Alpine hosts are refused). The distro's stock tinyproxy unit is masked; ours runs it.

The paths are the package's own because tinyproxy may be confined by path. Newer AppArmor profile
sets (Ubuntu 26.04's, on GitHub's runner image) have a profile `tinyproxy` for /usr/bin/tinyproxy, which
the kernel applies inside privileged containers too. It lets tinyproxy read /etc/tinyproxy/tinyproxy.conf
and /usr/share/tinyproxy/*, write /var/log/tinyproxy/tinyproxy.log and /run/tinyproxy/tinyproxy.pid, and
nothing else: a configuration in /etc/outpost was "Could not open config file" even for root. So the
configuration replaces the package's, the allow list sits next to the package's error pages (the one
other readable place), and the log is the package's log. Without a profile the same paths work as well.

The directives used, as documented in tinyproxy.conf(5) (https://tinyproxy.github.io/, the manual page
of the 1.11 series, docs/man5/tinyproxy.conf.txt.in in the source) and checked against src/conf.c,
src/reqs.c and src/filter.c of tinyproxy 1.11.0 to 1.11.2:

  User, Group          drop to this user when started as root (we start it as that user already)
  Port, Listen         one port on one address: the host's private address, not 0.0.0.0
  Timeout              seconds of inactivity before a connection is closed
  LogFile              a quoted path; quotes are required for string values since 1.11
  LogLevel Connect     "log connections without Info's noise": every request line, such as
                       "Request (file descriptor 7): CONNECT api.twilio.com:443 HTTP/1.1", is logged at
                       the Connect level, and a refusal ('Proxying refused on filtered domain
                       "example.com"') at Notice, which is above Connect, so both are kept
  MaxClients           threads, one per client
  Allow                client addresses or ranges; with any Allow line, everything else is denied
  ConnectPort          ports CONNECT may tunnel to; listing 443 and 80 refuses all others
  Filter               the allow list file; a line starting with # is a comment
  FilterURLs Off       match the destination host name, not the URL (the default; HTTPS has no URL)
  FilterType ere       extended regular expressions, so `(^|\\.)sendgrid\\.net$` works (1.11.1 and later;
                       1.10 and 1.11.0 only know the older `FilterExtended On`, which setup writes there)
  FilterDefaultDeny    Yes turns the filter list from a block list into an allow list
  DisableViaHeader     no Via header on plain-HTTP requests (HTTPS requests are opaque to the proxy)

Matching is case-insensitive (FilterCaseSensitive is off by default) and on the bare host name, without
the port. A refused name gets "HTTP/1.0 403 Filtered"; tinyproxy answers CONNECT with
"HTTP/1.0 200 Connection established" only after it has connected to the far end.
"""

import re
import shlex

from .. import versions
from ..config import ConfigError, ProxyConfig
from ..fetch import apt as aptmod, rpm as rpmmod
from . import clients, common, egress
from .clients import GiteaInfo, ProxyInfo

USER = common.PROXY_USER
CONF_PATH = "/etc/tinyproxy/tinyproxy.conf"          # the only configuration the AppArmor profile lets it read
ALLOW_PATH = "/usr/share/tinyproxy/outpost.allow"   # /usr/share/tinyproxy/* is readable; /etc/tinyproxy/* is not
LOG_DIR = "/var/log/tinyproxy"
LOG_PATH = f"{LOG_DIR}/tinyproxy.log"                # the only log file the profile lets it write
UNIT_PATH = "/etc/systemd/system/outpost-proxy.service"
LOGROTATE_PATH = "/etc/logrotate.d/tinyproxy"       # replaces the package's, which reloads the masked stock unit
OLD_PATHS = ("/etc/outpost/proxy.conf", "/etc/outpost/proxy.allow", "/etc/logrotate.d/outpost-proxy")   # written by earlier versions
STOCK_UNIT = "tinyproxy.service"
PACKAGE = "tinyproxy"
REFUSAL_PROBES = ("example.com", "example.net", "example.org")   # documentation domains, IANA reserved

# tinyproxy opens its log after dropping to USER, and the package's postinst gives the directory to its own
# user (tinyproxy:adm, 0750) on every upgrade; this runs as root before each start and hands it back
LOG_PREP = (f"mkdir -p {LOG_DIR} && chown {USER}:{USER} {LOG_DIR} && chmod 0750 {LOG_DIR} && "
            f"touch {LOG_PATH} && chown {USER}:{USER} {LOG_PATH} && chmod 0640 {LOG_PATH}")

LOGROTATE = f"""{LOG_PATH} {{
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}}
"""


# -- the allow list -----------------------------------------------------------------

def allow_regex(entry: str) -> str:
    """`api.twilio.com` -> `^api\\.twilio\\.com$`; `*.sendgrid.net` -> `(^|\\.)sendgrid\\.net$` (the domain
    and every name under it). Names only hold [a-z0-9.-], so escaping the dots is all the escaping needed."""
    if entry.startswith("*."):
        return "(^|\\.)" + entry[2:].replace(".", "\\.") + "$"
    return "^" + entry.replace(".", "\\.") + "$"


def allow_file(allow: list[str], gitea_name: str) -> str:
    lines = [
        "# Written by `outpost proxy setup` from servers.yml egress_proxy.allow. Edit there, not here.",
        "# One extended regular expression per line, matched case-insensitively against the host name a",
        "# client asks for (CONNECT host:443), never the URL. Everything that matches no line is refused.",
        "# The Gitea name is always allowed, so a client that ignores NO_PROXY still reaches Gitea, which",
        "# the seal lets every host reach anyway.",
        allow_regex(gitea_name),
        *[allow_regex(a) for a in allow if a != gitea_name],
    ]
    return "\n".join(lines) + "\n"


def allowed_by(allow: list[str], name: str) -> bool:
    return any(re.search(allow_regex(a), name) for a in allow)


def probe_name(allow: list[str]) -> str:
    """A name to test the proxy with: the first plain name, else the domain of the first wildcard."""
    plain = [a for a in allow if not a.startswith("*.")]
    return plain[0] if plain else allow[0][2:]


def refused_name(allow: list[str]) -> str:
    """A name the proxy must refuse: a documentation domain the allow list does not cover."""
    return next((n for n in REFUSAL_PROBES if not allowed_by(allow, n)), "refused.invalid")


# -- configuration ------------------------------------------------------------------

_IP_OR_NET = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?|[0-9a-fA-F:]+:[0-9a-fA-F:]*(/[0-9]{1,3})?")


def filter_type_line(version: str) -> str:
    """`FilterType ere` from tinyproxy 1.11.1 on; before that only `FilterExtended On` exists, and an
    unknown directive stops tinyproxy from starting."""
    nums = tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)
    return "FilterType ere" if nums >= (1, 11, 1) else "FilterExtended On"


def conf(listen_ip: str, port: int, client_allow: list[str], tinyproxy_version: str = "1.11.1") -> str:
    clients_ok = ["127.0.0.1", *clients.PRIVATE_NETS] + [a for a in client_allow if _IP_OR_NET.fullmatch(a)]
    allow_lines = "\n".join(f"Allow {a}" for a in dict.fromkeys(clients_ok))
    return f"""# Written by `outpost proxy setup`. Edit servers.yml (egress_proxy) and run it again instead.
# Directives: tinyproxy.conf(5), https://tinyproxy.github.io/
User {USER}
Group {USER}
Port {port}
Listen {listen_ip}
Timeout 600
MaxClients 100
LogFile "{LOG_PATH}"
# Connect: every request line with its host (CONNECT api.twilio.com:443), and refusals, logged at Notice
LogLevel Connect

# Who may use the proxy: this host, the private networks, and the infrastructures' extra addresses
{allow_lines}

# CONNECT (HTTPS tunnels) only to the web ports, the same two the firewall exception opens
ConnectPort 443
ConnectPort 80

# The allow list: refuse every host name the filter file does not match; match names, not URLs
Filter "{ALLOW_PATH}"
FilterURLs Off
{filter_type_line(tinyproxy_version)}
FilterDefaultDeny Yes

DisableViaHeader Yes
"""


def unit(binary: str = "/usr/bin/tinyproxy") -> str:
    return f"""[Unit]
Description=Outpost egress proxy (tinyproxy: only the names in {ALLOW_PATH})
Wants=network-online.target
After=network-online.target outpost-egress.service

[Service]
User={USER}
Group={USER}
ExecStartPre=+/bin/sh -c '{LOG_PREP}'
ExecStart={binary} -d -c {CONF_PATH}
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=2
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
"""


def info(cfg: ProxyConfig, gitea: GiteaInfo, gitea_host: str, ip: str | None = None) -> ProxyInfo:
    """How nodes reach the proxy. On the Gitea host it is http://<gitea name>:<port>, a name every node
    already resolves and an address every node already allows; elsewhere http://<egress_proxy.name>:<port>."""
    on_gitea = cfg.host == gitea_host
    name = gitea.name if on_gitea else cfg.name
    address = gitea.ip if on_gitea else ip
    if not address:
        raise RuntimeError(f"the egress proxy host {cfg.host} has no known address; run `outpost proxy setup`")
    no_proxy = [gitea.name, gitea.ip, "localhost", "127.0.0.1", *clients.PRIVATE_NETS] + ([] if on_gitea else [name, address])
    return ProxyInfo(url=f"http://{name}:{cfg.port}", no_proxy=",".join(dict.fromkeys(no_proxy)), ip=address, name=name,
                     allowed=tuple(cfg.allow), host=cfg.host, on_gitea=on_gitea)


# -- getting tinyproxy into the box ---------------------------------------------------

def package_need(cfg: ProxyConfig, facts: dict | None) -> tuple[str, str]:
    """(kind, distro) of the proxy host: `egress_proxy.distro`, else what `outpost hosts` cached about it."""
    if cfg.distro:
        if cfg.distro in aptmod.DEBIAN | aptmod.UBUNTU:
            return "apt", cfg.distro
        try:
            rpmmod.split(cfg.distro)
        except ValueError:
            raise ConfigError(f"egress_proxy.distro {cfg.distro!r} is neither a Debian or Ubuntu codename (bookworm, noble) "
                              "nor an rpm distro (rocky9, alma9, fedora40, stream9)") from None
        return "rpm", cfg.distro
    if facts and facts.get("id"):
        family = common.os_family(facts)
        if family == "apk":
            raise ConfigError(f"the egress proxy host {cfg.host} runs Alpine, which Outpost does not support as a proxy host; "
                              "put the proxy on a Debian, Ubuntu or RHEL-family host (egress_proxy.host)")
        if family == "apt" and facts.get("codename"):
            return "apt", facts["codename"]
        if family == "rpm":
            distro = rpmmod.distro_for_host(facts.get("id", ""), facts.get("version_id", ""))
            if distro:
                return "rpm", distro
    raise ConfigError(f"Outpost does not know which system the egress proxy host {cfg.host} runs, so it cannot fetch tinyproxy "
                      "for it. Set `distro:` under egress_proxy in servers.yml (noble, bookworm, rocky9...) or run "
                      "`outpost hosts --refresh` once so Outpost can ask the host.")


def add_package(needs, cfg: ProxyConfig | None, facts: dict | None):
    """Add tinyproxy to the apt or rpm needs of the proxy host's distro, as a platform package."""
    if cfg:
        kind, distro = package_need(cfg, facts)
        pkgs = getattr(needs, kind).setdefault(distro, [])
        if PACKAGE not in pkgs:
            pkgs.append(PACKAGE)
    return needs


# -- setup --------------------------------------------------------------------------

def _distro_of(family: str, facts: dict) -> str:
    if family == "rpm":
        return rpmmod.distro_for_host(facts.get("id", ""), facts.get("version_id", "")) or facts.get("id", "?")
    return facts.get("codename") or facts.get("id", "?")


INSTALL = {
    "apt": ("apt-get update -qq 2>&1 | tail -3\n"
            f"if ! apt-cache show {PACKAGE} >/dev/null 2>&1; then echo OUTPOST-NO-TINYPROXY; exit 0; fi\n"
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Dpkg::Options::=--force-confold {PACKAGE} 2>&1 | tail -3\n"),
    "rpm": ("dnf -q makecache 2>&1 | tail -3\n"
            f"if ! dnf -q info {PACKAGE} >/dev/null 2>&1; then echo OUTPOST-NO-TINYPROXY; exit 0; fi\n"
            f"dnf install -y -q {PACKAGE} 2>&1 | tail -3\n"),
}
REGISTRY = {"apt": ("Debian", "apt"), "rpm": ("RPM", "rpm")}


def diagnose_script(family: str, gitea: GiteaInfo, distro: str) -> str:
    """What the package manager saw when it found no tinyproxy: the refresh in full, the package source
    Outpost wrote, what the index says about tinyproxy, and whether Gitea answers for the distro at all."""
    keep_going = "set +e +o pipefail\n"   # Host.run starts with set -euo pipefail; one failing probe must not end the report
    if family == "rpm":
        url = f"{gitea.rpm_repo}/{distro}/repodata/repomd.xml"
        return (keep_going +
                f"echo '--- dnf makecache'; dnf makecache 2>&1 | tail -20\n"
                f"echo '--- {clients.PATH_RPM_REPO}'; cat {clients.PATH_RPM_REPO} 2>&1\n"
                f"echo '--- dnf repolist'; dnf repolist -v 2>&1 | tail -20\n"
                f"echo '--- dnf info {PACKAGE}'; dnf info {PACKAGE} 2>&1 | tail -10\n"
                f"echo '--- {url}'; curl --cacert {clients.PATH_CA} -sS -o /dev/null -w '%{{http_code}}\\n' --max-time 10 {shlex.quote(url)} 2>&1\n")
    url = f"{gitea.apt_repo}/dists/{distro}/Release"
    return (keep_going +
            f"echo '--- apt-get update'; apt-get update 2>&1 | tail -20\n"
            f"echo '--- /etc/apt/sources.list.d/outpost.list'; cat /etc/apt/sources.list.d/outpost.list 2>&1\n"
            f"echo '--- other sources'; ls /etc/apt/sources.list /etc/apt/sources.list.d/ 2>&1\n"
            f"echo '--- apt-cache policy {PACKAGE}'; apt-cache policy {PACKAGE} 2>&1\n"
            f"echo '--- {url}'; curl --cacert {clients.PATH_CA} -sS -o /dev/null -w '%{{http_code}}\\n' --max-time 10 {shlex.quote(url)} 2>&1\n"
            f"echo '--- getent hosts {gitea.name}'; getent hosts {shlex.quote(gitea.name)} 2>&1\n")


def setup(host, ctx: common.NodeContext, cfg: ProxyConfig, *, egress_allow: list[str], client_allow: list[str], log=print) -> dict:
    """Install and seal the proxy on `host`. ctx.proxy says where it is; on the Gitea host nothing of a
    node is prepared (it already resolves its own name, and reaches Gitea on itself), elsewhere the host
    is prepared like a node first, so it trusts the CA, resolves Gitea and gets packages from it."""
    p = ctx.proxy
    if not p:
        raise RuntimeError("proxy.setup needs ctx.proxy")
    g = ctx.gitea
    log(f"==> [1/6] connecting to {host.alias}")
    facts = host.facts()
    family = common.os_family(facts)
    if family == "apk":
        raise RuntimeError(f"[{host.alias}] runs Alpine, which Outpost does not support as an egress proxy host. "
                           "Put the proxy on a Debian, Ubuntu or RHEL-family host (egress_proxy.host in servers.yml).")
    want = versions.names()["uname"]
    if facts.get("arch") and facts["arch"] != want:
        raise RuntimeError(f"[{host.alias}] is {facts['arch']} but this platform is built for {want} (arch: {versions.ARCH} in servers.yml).")
    distro = _distro_of(family, facts)
    log(f"  [{host.alias}] {facts.get('id', '?')} {distro}, proxy {p.url} ({'on the Gitea host' if p.on_gitea else 'its own host'})")

    log(f"==> [2/6] package sources: Gitea's {REGISTRY[family][0]} registry")
    if p.on_gitea:
        # the Gitea host is not a node: give it just what apt or dnf needs to install from Gitea itself
        host.write(clients.PATH_CA, ctx.ca_pem)
        if family == "apt":
            host.write("/etc/apt/apt.conf.d/99outpost", f'Acquire::https::CaInfo "{clients.PATH_CA}";\n')
        {"apt": common._configure_apt, "rpm": common._configure_rpm}[family](host, ctx, facts)
    else:
        common.prepare_node(host, ctx, log)

    log(f"==> [3/6] {PACKAGE} from Gitea")
    out = host.run(f"systemctl mask {STOCK_UNIT} >/dev/null 2>&1 || true\n" + INSTALL[family], check=False, timeout=900)
    if "OUTPOST-NO-TINYPROXY" in out.stdout:
        name, kind = REGISTRY[family]
        seen = host.run(diagnose_script(family, g, distro), check=False, timeout=300)
        detail = (seen.stdout + seen.stderr).strip()[-3000:]
        raise RuntimeError(f"Gitea's {name} registry has no {PACKAGE} for {distro}. Run `outpost fetch --only {kind}` and "
                           f"`outpost upload` (with egress_proxy in servers.yml, fetch adds {PACKAGE} for {distro} by itself), "
                           f"then `outpost proxy setup` again.\nWhat [{host.alias}] sees:\n{detail}")
    if not out.ok:
        raise RuntimeError(f"[{host.alias}] installing {PACKAGE} from Gitea failed: {(out.stderr or out.stdout).strip()[-400:]}")
    found = host.run(f"systemctl disable --now {STOCK_UNIT} >/dev/null 2>&1 || true\n"
                     f"command -v {PACKAGE} || ls /usr/sbin/{PACKAGE} /usr/bin/{PACKAGE} 2>/dev/null | head -1 || true\n"
                     f"{PACKAGE} -v 2>&1 | head -1 || true").stdout.split()
    binary = next((w for w in found if w.startswith("/")), f"/usr/bin/{PACKAGE}")
    version = next((w for w in reversed(found) if re.fullmatch(r"\d+\.\d+(\.\d+)?\S*", w)), "")
    log(f"  [{host.alias}] {PACKAGE} {version or '(version unknown)'} at {binary}, stock {STOCK_UNIT} masked")

    log(f"==> [4/6] user {USER}, configuration, allow list")
    host.run(
        f"getent group {USER} >/dev/null || groupadd --system {USER}\n"
        f"id -u {USER} >/dev/null 2>&1 || useradd --system --gid {USER} --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin {USER}\n"
        f"mkdir -p /etc/tinyproxy /usr/share/tinyproxy && chmod 0755 /etc/tinyproxy /usr/share/tinyproxy && {LOG_PREP}\n"
        f"rm -f {' '.join(OLD_PATHS)}\n"
    )
    host.write(CONF_PATH, conf(p.ip, cfg.port, client_allow, version or "1.11.1"))
    host.write(ALLOW_PATH, allow_file(list(cfg.allow), g.name))
    host.write(LOGROTATE_PATH, LOGROTATE)
    readable = host.run(f"runuser -u {USER} -- test -r {CONF_PATH} && runuser -u {USER} -- test -r {ALLOW_PATH} && "
                        f"runuser -u {USER} -- test -w {LOG_PATH}", check=False)
    if not readable.ok:
        raise RuntimeError(f"[{host.alias}] {USER} cannot read its configuration or write its log:\n{_access_report(host)}")
    log(f"  [{host.alias}] allows {', '.join(cfg.allow)} (and {g.name}); everything else gets 403")

    log("==> [5/6] service outpost-proxy")
    host.write(UNIT_PATH, unit(binary))
    started = host.run("systemctl daemon-reload && systemctl enable outpost-proxy >/dev/null 2>&1; "
                       "systemctl restart outpost-proxy && sleep 1 && systemctl is-active outpost-proxy", check=False)
    if not started.ok:
        journal = host.run("journalctl -u outpost-proxy -n 15 --no-pager 2>&1 || true", check=False).stdout.strip()
        raise RuntimeError(f"[{host.alias}] outpost-proxy did not start:\n{journal[-800:]}\n{_access_report(host, binary)}")

    log("==> [6/6] sealing, with the one exception for the proxy's own user")
    egress.install(host, g.ip, egress_allow, proxy_user=USER)
    log(f"  [{host.alias}] egress: {egress.seal(host, 'proxy setup')}; {USER} may open TCP 443 and 80, nothing else changed")
    return {"host": host.alias, "ip": p.ip, "port": cfg.port, "name": p.name, "url": p.url, "on_gitea": p.on_gitea,
            "allow": list(cfg.allow), "tinyproxy": version, "family": family, "distro": distro}


def access_script(binary: str | None = None) -> str:
    """Why tinyproxy may not get at its files: every directory on the way with its mode, what the proxy user
    can read, and, with `binary`, tinyproxy run by hand as that user outside systemd's sandbox for two seconds."""
    lines = ["set +e +o pipefail",
             *[f"echo '--- namei -l {p}'; namei -l {p} 2>&1" for p in (CONF_PATH, ALLOW_PATH, LOG_PATH)],
             f"echo '--- id {USER}'; id {USER} 2>&1",
             f"echo '--- as {USER}'; runuser -u {USER} -- head -c 80 {CONF_PATH} >/dev/null 2>/tmp/outpost-proxy-read.err && echo readable || cat /tmp/outpost-proxy-read.err"]
    if binary:
        lines.append(f"echo '--- {binary} -d -c {CONF_PATH} as {USER}, outside systemd'; "
                     f"timeout 2 runuser -u {USER} -- {binary} -d -c {CONF_PATH} 2>&1 | tail -5; echo \"exit ${{PIPESTATUS[0]}}\"")
        # the file is readable by the user but tinyproxy cannot open it: tell a path-based MAC (AppArmor on the
        # kernel, which a privileged container shares) from anything else
        lines += [
            f"echo '--- as root'; timeout 2 {binary} -d -c {CONF_PATH} 2>&1 | tail -2; echo \"exit ${{PIPESTATUS[0]}}\"",
            f"cp {CONF_PATH} /tmp/outpost-proxy-copy.conf && chmod 0644 /tmp/outpost-proxy-copy.conf; "
            f"echo '--- copy in /tmp, as {USER}'; timeout 2 runuser -u {USER} -- {binary} -d -c /tmp/outpost-proxy-copy.conf 2>&1 | tail -2; echo \"exit ${{PIPESTATUS[0]}}\"",
            f"echo '--- ls -l {binary}'; ls -lL {binary} 2>&1; getcap {binary} 2>&1",
            "echo '--- apparmor'; cat /sys/module/apparmor/parameters/enabled 2>&1; "
            "grep -i tinyproxy /sys/kernel/security/apparmor/profiles 2>&1 | head -5; cat /proc/self/attr/current 2>&1",
            "echo '--- kernel denials'; dmesg 2>&1 | grep -iE 'denied|apparmor' | tail -8",
        ]
        lines.append("echo '--- systemctl show outpost-proxy'; systemctl show outpost-proxy -p User -p ProtectSystem -p ProtectHome -p PrivateTmp 2>&1")
    return "\n".join(lines) + "\n"


def _access_report(host, binary: str | None = None) -> str:
    out = host.run(access_script(binary), check=False, timeout=60)
    return (out.stdout + out.stderr).strip()[-2000:]


# -- verify -------------------------------------------------------------------------

def host_checks(ctx: common.NodeContext, cfg: ProxyConfig) -> list[tuple[str, str]]:
    """Checks on the proxy host itself; each is one shell command that must succeed (verify.script)."""
    from .verify import by_family
    g = ctx.gitea
    target = probe_name(list(cfg.allow))
    owner = f"-p tcp -m owner --uid-owner {USER} -m tcp --dport 443 -j RETURN"
    return [
        ("proxy service active", "systemctl is-active outpost-proxy"),
        ("tinyproxy installed from gitea", by_family(
            apt=f"dpkg -s {PACKAGE} >/dev/null 2>&1 && apt-cache policy {PACKAGE} | grep -q {shlex.quote(g.apt_repo)} && {PACKAGE} -v",
            apk="echo 'Alpine is not supported as a proxy host'; false",
            rpm=f"rpm -q {PACKAGE} && dnf -q list --installed {PACKAGE} 2>/dev/null | grep -q '@outpost-'")),
        ("proxy allow list is the current one", f"printf %s {shlex.quote(allow_file(list(cfg.allow), g.name))} | cmp -s - {ALLOW_PATH} && echo {len(cfg.allow)} names"),
        ("egress sealed", f"test \"$({egress.SCRIPT_PATH} status)\" = sealed"),
        ("proxy exception only for its own user",
         f"iptables -C {egress.OUT_CHAIN} {owner} && iptables -t security -C {egress.OUT_CHAIN} {owner} && "
         f"test \"$(iptables -S {egress.OUT_CHAIN} | grep -c -- '-j RETURN')\" = 2"),
        ("public internet rejected for other users", "! curl --noproxy '*' -sS --max-time 5 https://pypi.org/simple/ >/dev/null 2>&1"),
        ("proxy user can reach allowed names",
         f"code=$(runuser -u {USER} -- curl --noproxy '*' -sS --max-time 10 -o /dev/null -w '%{{http_code}}' https://{target}/ 2>/dev/null); "
         f"case \"$code\" in [234]??) echo \"{target} -> $code\" ;; *) echo \"{target} -> ${{code:-no answer}}\"; false ;; esac"),
        ("proxy log exists", f"test -f {LOG_PATH} && echo \"$(wc -l < {LOG_PATH}) lines in {LOG_PATH}\""),
    ]


def verify(host, ctx: common.NodeContext, cfg: ProxyConfig) -> list[dict]:
    from . import verify as verifymod
    checks = host_checks(ctx, cfg) + verifymod.proxy_checks(ctx)
    result = host.run(verifymod.script(checks), check=False, timeout=300)
    parsed = verifymod.parse(result.stdout)
    return parsed or [{"name": "connect", "ok": False, "detail": result.stderr.strip()[-300:] or "no output"}]
