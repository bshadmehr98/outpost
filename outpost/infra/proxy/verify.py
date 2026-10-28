"""Verifications of the egress proxy: on the proxy host itself, and from every node that may use it."""

import shlex

from outpost import egress
from outpost.client_config import PATH_PROXY_ENV
from outpost.config import ProxyConfig
from outpost.infra.context import NodeContext
from outpost.infra.proxy.allow_list import allow_file, probe_name, refused_name
from outpost.infra.proxy.tinyproxy import ALLOW_PATH, LOG_PATH, USER
from outpost.infra.verify import by_family, run_verifications
from outpost.needs import PROXY_PACKAGE
from outpost.ssh import Host


def verify_proxy_host(host: Host, ctx: NodeContext, cfg: ProxyConfig) -> list[dict]:
    """Run the proxy host's verifications on it: host_verifications, then proxy_verifications (without
    the node's proxy.env one when the proxy host is the Gitea server, which is no node)."""
    on_gitea = ctx.proxy.on_gitea  # pyright: ignore[reportOptionalMemberAccess]  verify runs only with an egress proxy
    verifications = host_verifications(ctx, cfg) + proxy_verifications(ctx, node=not on_gitea)
    return run_verifications(host, verifications, timeout=300)


def proxy_verifications(ctx: NodeContext, node: bool = True) -> list[tuple[str, str]]:
    """With an egress proxy, what proves it works from a node (none without one).

    CONNECT to an allowed name is answered 200 by the proxy, and the far end then answers over the
    tunnel (any HTTP status: it was reached). CONNECT to a name not on the list is answered by the proxy
    itself with 403. %{http_connect} is the proxy's answer to CONNECT, so the second verification passes
    exactly when the proxy refused, not when the name was merely unreachable.

    Args:
        node: False for the Gitea server running the proxy: it is not a node and gets no proxy.env.
    """
    p = ctx.proxy
    if not p:
        return []
    allowed, other = probe_name(list(p.allowed)), refused_name(list(p.allowed))
    url = shlex.quote(p.url)
    settings = [
        (
            "proxy settings for programs that opt in",
            f"grep -qx {shlex.quote('HTTPS_PROXY=' + p.url)} {PATH_PROXY_ENV}",
        )
    ]
    return (settings if node else []) + [
        (
            "proxy reachable",
            f"out=$(curl -x {url} -sS --max-time 15 -o /dev/null -w '%{{http_connect}} %{{http_code}}' https://{allowed}/ 2>/dev/null); "
            f'case "$out" in \'200 \'[12345]??) echo "{allowed} through the proxy -> ${{out#* }}" ;; '
            f'*) echo "{allowed} through {p.url} -> CONNECT/HTTP ${{out:-no answer}}"; false ;; esac',
        ),
        (
            "proxy refuses unknown names",
            f"code=$(curl -x {url} -sS --max-time 15 -o /dev/null -w '%{{http_connect}}' https://{other}/ 2>/dev/null); "
            f'test "$code" = 403 && echo "CONNECT {other} -> 403" || {{ echo "CONNECT {other} -> ${{code:-no answer}}, expected 403 from the proxy"; false; }}',
        ),
    ]


def host_verifications(ctx: NodeContext, cfg: ProxyConfig) -> list[tuple[str, str]]:
    """The verifications on the proxy host itself."""
    g = ctx.gitea
    target = probe_name(list(cfg.allow))
    owner = f"-p tcp -m owner --uid-owner {USER} -m tcp --dport 443 -j RETURN"
    return [
        ("proxy service active", "systemctl is-active outpost-proxy"),
        (
            "tinyproxy installed from gitea",
            by_family(
                apt=f"dpkg -s {PROXY_PACKAGE} >/dev/null 2>&1 && apt-cache policy {PROXY_PACKAGE} | grep -q {shlex.quote(g.apt_repo)} && {PROXY_PACKAGE} -v",
                apk="echo 'Alpine is not supported as a proxy host'; false",
                rpm=f"rpm -q {PROXY_PACKAGE} && dnf -q list --installed {PROXY_PACKAGE} 2>/dev/null | grep -q '@outpost-'",
            ),
        ),
        (
            "proxy allow list is the current one",
            f"printf %s {shlex.quote(allow_file(list(cfg.allow), g.name))} | cmp -s - {ALLOW_PATH} && echo {len(cfg.allow)} names",
        ),
        ("egress sealed", f'test "$({egress.SCRIPT_PATH} status)" = sealed'),
        (
            "proxy exception only for its own user",
            f"iptables -C {egress.OUT_CHAIN} {owner} && iptables -t security -C {egress.OUT_CHAIN} {owner} && "
            f"test \"$(iptables -S {egress.OUT_CHAIN} | grep -c -- '-j RETURN')\" = 2",
        ),
        (
            "public internet rejected for other users",
            "! curl --noproxy '*' -sS --max-time 5 https://pypi.org/simple/ >/dev/null 2>&1",
        ),
        (
            "proxy user can reach allowed names",
            f"code=$(runuser -u {USER} -- curl --noproxy '*' -sS --max-time 10 -o /dev/null -w '%{{http_code}}' https://{target}/ 2>/dev/null); "
            f'case "$code" in [234]??) echo "{target} -> $code" ;; *) echo "{target} -> ${{code:-no answer}}"; false ;; esac',
        ),
        ("proxy log exists", f'test -f {LOG_PATH} && echo "$(wc -l < {LOG_PATH}) lines in {LOG_PATH}"'),
    ]
