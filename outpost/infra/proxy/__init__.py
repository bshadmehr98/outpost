"""The egress proxy: named exceptions to the seal, through one logged tinyproxy.

    set_up_egress_proxy(system, log=print)    # `outpost proxy setup`: the proxy host, then the set-up nodes
    verify_egress_proxy(system, log=print)    # `outpost proxy verify`: True when every verification passed

The seal (outpost/egress.py) is address-based: iptables cannot match host names, and the addresses
behind a CDN change. So the few third-party services sealed workloads may call (api.twilio.com, an
email API) are reached through one outbound forward proxy that checks the *name* a client asks for:

    container --HTTPS_PROXY--> tinyproxy on a private address --only allowed names--> the internet
                   CONNECT api.twilio.com:443                  TLS stays end to end

On the proxy host, the seal has one exception: user outpost-proxy may open TCP 443 and 80
(egress.py, chain OUTPOST-OUT, OUTPUT only). tinyproxy.py says what else lands there.

    recipe.py       set_up_egress_proxy, verify_egress_proxy, update_set_up_nodes
    setup.py        set_up_on_host: six steps, `==> [n/6]` (connect_and_check_host, point_packages_at_gitea,
                    install_tinyproxy, write_user_and_config, start_service, seal_with_exception)
    verify.py       verify_proxy_host, host_verifications, proxy_verifications (also run by every node)
    tinyproxy.py    paths, conf(), unit(), filter_type_line(), LOG_PREP, LOGROTATE, INSTALL
    allow_list.py   allow_regex, allow_file, allowed_by, probe_name, refused_name
    diagnose.py     diagnose_script, access_script, access_report: what to show when it fails

Code outside this folder uses only the names below.
"""

from outpost.infra.proxy.allow_list import allow_file, allow_regex, allowed_by, probe_name, refused_name
from outpost.infra.proxy.diagnose import access_report, access_script, diagnose_script
from outpost.infra.proxy.recipe import set_up_egress_proxy, update_set_up_nodes, verify_egress_proxy
from outpost.infra.proxy.setup import set_up_on_host
from outpost.infra.proxy.tinyproxy import (
    ALLOW_PATH,
    CONF_PATH,
    LOG_PATH,
    LOG_PREP,
    LOGROTATE,
    USER,
    conf,
    filter_type_line,
    unit,
)
from outpost.infra.proxy.verify import host_verifications, proxy_verifications, verify_proxy_host

__all__ = [
    "ALLOW_PATH",
    "CONF_PATH",
    "LOGROTATE",
    "LOG_PATH",
    "LOG_PREP",
    "USER",
    "access_report",
    "access_script",
    "allow_file",
    "allow_regex",
    "allowed_by",
    "conf",
    "diagnose_script",
    "filter_type_line",
    "host_verifications",
    "probe_name",
    "proxy_verifications",
    "refused_name",
    "set_up_egress_proxy",
    "set_up_on_host",
    "unit",
    "update_set_up_nodes",
    "verify_egress_proxy",
    "verify_proxy_host",
]
