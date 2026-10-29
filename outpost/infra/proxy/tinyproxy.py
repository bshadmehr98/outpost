"""tinyproxy on the proxy host: its paths, its configuration, its unit, and how to install it.

  /etc/tinyproxy/tinyproxy.conf        tinyproxy configuration (conf), in place of the package's
  /usr/share/tinyproxy/outpost.allow   one anchored extended regex per allowed name (allow_list.py)
  /var/log/tinyproxy/tinyproxy.log     every CONNECT with its host name, and every refusal
  outpost-proxy.service                tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf as user outpost-proxy

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

from outpost.client_config import PRIVATE_NETS
from outpost.egress import PROXY_USER
from outpost.needs import PROXY_PACKAGE

USER = PROXY_USER
CONF_PATH = "/etc/tinyproxy/tinyproxy.conf"  # the only configuration the AppArmor profile lets it read
ALLOW_PATH = "/usr/share/tinyproxy/outpost.allow"  # /usr/share/tinyproxy/* is readable; /etc/tinyproxy/* is not
LOG_DIR = "/var/log/tinyproxy"
LOG_PATH = f"{LOG_DIR}/tinyproxy.log"  # the only log file the profile lets it write
UNIT_PATH = "/etc/systemd/system/outpost-proxy.service"
LOGROTATE_PATH = "/etc/logrotate.d/tinyproxy"  # replaces the package's, which reloads the masked stock unit
OLD_PATHS = (
    "/etc/outpost/proxy.conf",
    "/etc/outpost/proxy.allow",
    "/etc/logrotate.d/outpost-proxy",
)  # written by earlier versions
STOCK_UNIT = "tinyproxy.service"
NO_PACKAGE_MARKER = "OUTPOST-NO-TINYPROXY"  # what INSTALL prints when Gitea has no tinyproxy for the distro

# tinyproxy opens its log after dropping to USER, and the package's postinst gives the directory to its own
# user (tinyproxy:adm, 0750) on every upgrade; this runs as root before each start and hands it back
LOG_PREP = (
    f"mkdir -p {LOG_DIR} && chown {USER}:{USER} {LOG_DIR} && chmod 0750 {LOG_DIR} && "
    f"touch {LOG_PATH} && chown {USER}:{USER} {LOG_PATH} && chmod 0640 {LOG_PATH}"
)

LOGROTATE = f"""{LOG_PATH} {{
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}}
"""

# Install the package from Gitea, by family; print NO_PACKAGE_MARKER when the index has no tinyproxy.
INSTALL = {
    "apt": (
        "apt-get update -qq 2>&1 | tail -3\n"
        f"if ! apt-cache show {PROXY_PACKAGE} >/dev/null 2>&1; then echo {NO_PACKAGE_MARKER}; exit 0; fi\n"
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Dpkg::Options::=--force-confold {PROXY_PACKAGE} 2>&1 | tail -3\n"
    ),
    "rpm": (
        "dnf -q makecache 2>&1 | tail -3\n"
        f"if ! dnf -q info {PROXY_PACKAGE} >/dev/null 2>&1; then echo {NO_PACKAGE_MARKER}; exit 0; fi\n"
        f"dnf install -y -q {PROXY_PACKAGE} 2>&1 | tail -3\n"
    ),
}
REGISTRY = {"apt": ("Debian", "apt"), "rpm": ("RPM", "rpm")}  # family -> (Gitea's registry, the needs kind)

_IP_OR_NET = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?|[0-9a-fA-F:]+:[0-9a-fA-F:]*(/[0-9]{1,3})?")


def conf(listen_ip: str, port: int, client_allow: list[str], tinyproxy_version: str = "1.11.1") -> str:
    """The text of /etc/tinyproxy/tinyproxy.conf.

    Args:
        listen_ip: the proxy host's private address.
        client_allow: more addresses that may use the proxy; entries that are not addresses or networks are left out.
        tinyproxy_version: picks the filter directive (filter_type_line).
    """
    clients_ok = ["127.0.0.1", *PRIVATE_NETS] + [a for a in client_allow if _IP_OR_NET.fullmatch(a)]
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


def filter_type_line(version: str) -> str:
    """`FilterType ere` from tinyproxy 1.11.1 on; before that only `FilterExtended On` exists, and an
    unknown directive stops tinyproxy from starting."""
    numbers = [int(x) for x in re.findall(r"\d+", version)[:3]]  # "1.11.1-2" -> [1, 11, 1]
    return "FilterType ere" if numbers >= [1, 11, 1] else "FilterExtended On"


def unit(binary: str = "/usr/bin/tinyproxy") -> str:
    """The text of outpost-proxy.service: tinyproxy as USER, its log handed back to USER before each start."""
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
