"""Prove a node uses Gitea and nothing else. Each check is one shell command that must succeed.

Output is a list of {name, ok, detail}. The checks that matter most:
  - the node reaches Gitea over TLS it trusts
  - the public internet is rejected, and the seal comes back after a reboot
  - every package client is configured to use Gitea, and only Gitea (apt, apk or dnf, whichever the node has)
  - with an egress proxy: the proxy lets an allowed name through and refuses any other
"""

import shlex

from .. import versions
from . import clients, common


def by_family(apt: str, apk: str, rpm: str) -> str:
    """One shell snippet that runs the right command for the node's family, read from /etc/os-release,
    so the same script (and the same check names) works on Debian, Ubuntu, Alpine and the RHEL family.
    The case's exit status is that of the branch taken."""
    return (". /etc/os-release 2>/dev/null; case \" $ID $ID_LIKE \" in "
            f"*alpine*) {apk} ;; *rhel*|*fedora*|*centos*|*rocky*|*almalinux*) {rpm} ;; *) {apt} ;; esac")


def source_commands(g: clients.GiteaInfo) -> dict[str, str]:
    """At least one source points at Gitea, and none points anywhere else. Both halves matter: from 0.1.0
    the apt probe in setup used HEAD, which Gitea's Debian registry does not answer, so outpost.list was
    empty on every apt node, and a check that only looked for foreign sources passed on an empty list."""
    def gitea_count(what: str, count: str, fix: str) -> str:
        return (f"{{ n=$({count}); if [ \"$n\" -gt 0 ]; then echo \"{what} from gitea: $n\"; "
                f"else echo {shlex.quote(f'no {what} point at gitea: {fix}')}; false; fi; }}")
    fix = "is this release uploaded to Gitea? `outpost fetch` and `outpost upload` it, then `outpost infra setup` again"
    apt_lines = "grep -rhsE '^(deb |URIs:)' /etc/apt/sources.list /etc/apt/sources.list.d/"
    return dict(
        apt=(f"! grep -rhs '^deb ' /etc/apt/sources.list /etc/apt/sources.list.d/ | grep -v {g.url} && "
             f"! grep -rhs '^URIs:' /etc/apt/sources.list.d/ | grep -v {g.url} && "
             + gitea_count("apt sources", f"{apt_lines} | grep -c {shlex.quote(g.apt_repo)}", f"/etc/apt/sources.list.d/outpost.list has no deb line; {fix}")),
        apk=(f"! grep -v '^#' {clients.PATH_APK_REPOS} | grep -v '^$' | grep -v {g.url} && "
             + gitea_count("apk repositories", f"grep -v '^#' {clients.PATH_APK_REPOS} | grep -c {shlex.quote(g.apk_repo)}", f"{clients.PATH_APK_REPOS} is empty; {fix}")),
        rpm=("! grep -lsE '^enabled *= *1' /etc/yum.repos.d/*.repo | grep -v outpost.repo && "
             + gitea_count("rpm repositories", f"grep -csE '^baseurl *= *{g.rpm_repo}/' {clients.PATH_RPM_REPO}", f"{clients.PATH_RPM_REPO} has no section; {fix}")),
    )


def common_checks(ctx: common.NodeContext) -> list[tuple[str, str]]:
    g = ctx.gitea
    # the tool's own exit status, not tail's: a source that fails to download fails the check
    index_updates = by_family(
        apt="out=$(apt-get update -qq 2>&1); rc=$?; echo \"$out\" | tail -3; test $rc -eq 0",
        apk="out=$(apk update 2>&1); rc=$?; echo \"$out\" | tail -3; test $rc -eq 0",
        rpm="out=$(dnf makecache -q 2>&1); rc=$?; echo \"$out\" | tail -3; test $rc -eq 0",
    )
    return [
        ("gitea in /etc/hosts", f"getent hosts {g.name} | grep -q {g.ip}"),
        ("gitea name restored at boot", "systemctl is-enabled outpost-hosts.service"),
        ("gitea reachable with trusted TLS", f"curl --cacert {clients.PATH_CA} -fsS --max-time 10 {g.url}/api/v1/version"),
        ("system trust store has the CA", f"curl -fsS --max-time 10 {g.url}/api/v1/version"),
        ("pip points at gitea", f"grep -q {g.url} /etc/pip.conf && ! grep -rqs extra-index-url /etc/pip.conf /root/.pip /root/.config/pip"),
        ("npm points at gitea", f"grep -q {g.url} /etc/npmrc"),
        ("package sources are gitea only", by_family(**source_commands(g))),
        ("node dns for containers running", "systemctl is-active outpost-dns"),
        ("package index updates offline", index_updates),
        ("egress sealed", "test \"$(/usr/local/sbin/outpost-egress status)\" = sealed"),
        ("public internet rejected", "! curl --noproxy '*' -sS --max-time 5 https://pypi.org/simple/ >/dev/null 2>&1"),
        ("egress seal survives reboot", "systemctl is-enabled outpost-egress.service"),
        ("routed traffic sealed too (containers, pods)", "iptables -C FORWARD -j OUTPOST && iptables -t security -C FORWARD -j OUTPOST"),
        ("egress script is the current one", "grep -q 'build_chain $ipt security DROP' /usr/local/sbin/outpost-egress"),
        (f"coredns at pinned version {versions.COREDNS}", f"/usr/local/bin/coredns -version | grep -q {versions.COREDNS}"),
        ("pip.conf unchanged", f"test \"$(grep -c . /etc/pip.conf)\" = 3 && grep -q '^cert = {clients.PATH_CA}' /etc/pip.conf"),
    ] + proxy_checks(ctx)


def proxy_checks(ctx: common.NodeContext) -> list[tuple[str, str]]:
    """With an egress proxy: CONNECT to an allowed name is answered 200 by the proxy and the far end then
    answers over the tunnel (any HTTP status: it was reached), and CONNECT to a name not on the list is
    answered by the proxy itself with 403. %{http_connect} is the proxy's answer to CONNECT, so the second
    check passes exactly when the proxy refused, not when the name was merely unreachable."""
    from . import proxy as proxymod
    p = ctx.proxy
    if not p:
        return []
    allowed, other = proxymod.probe_name(list(p.allowed)), proxymod.refused_name(list(p.allowed))
    url = shlex.quote(p.url)
    return [
        ("proxy settings for programs that opt in", f"grep -qx {shlex.quote('HTTPS_PROXY=' + p.url)} {clients.PATH_PROXY_ENV}"),
        ("proxy reachable",
         f"out=$(curl -x {url} -sS --max-time 15 -o /dev/null -w '%{{http_connect}} %{{http_code}}' https://{allowed}/ 2>/dev/null); "
         f"case \"$out\" in '200 '[12345]??) echo \"{allowed} through the proxy -> ${{out#* }}\" ;; "
         f"*) echo \"{allowed} through {p.url} -> CONNECT/HTTP ${{out:-no answer}}\"; false ;; esac"),
        ("proxy refuses unknown names",
         f"code=$(curl -x {url} -sS --max-time 15 -o /dev/null -w '%{{http_connect}}' https://{other}/ 2>/dev/null); "
         f"test \"$code\" = 403 && echo \"CONNECT {other} -> 403\" || {{ echo \"CONNECT {other} -> ${{code:-no answer}}, expected 403 from the proxy\"; false; }}"),
    ]


def script(checks: list[tuple[str, str]]) -> str:
    lines = ["set +e"]
    for name, cmd in checks:
        lines.append(
            f"out=$( ( {cmd} ) 2>&1 ); rc=$?; "
            f"if [ $rc -eq 0 ]; then echo \"CHECK|{name}|OK|$(echo \"$out\" | tail -1)\"; else echo \"CHECK|{name}|FAIL|$(echo \"$out\" | tail -1)\"; fi"
        )
    return "\n".join(lines) + "\n"


def parse(output: str) -> list[dict]:
    results = []
    for line in output.splitlines():
        if line.startswith("CHECK|"):
            _, name, status, detail = line.split("|", 3)
            results.append({"name": name, "ok": status == "OK", "detail": detail.strip()})
    return results


def verify_node(host, ctx: common.NodeContext, extra: list[tuple[str, str]]) -> list[dict]:
    checks = common_checks(ctx) + extra
    result = host.run(script(checks), check=False, timeout=600)
    parsed = parse(result.stdout)
    if not parsed:
        parsed = [{"name": "connect", "ok": False, "detail": result.stderr.strip()[-300:] or "no output"}]
    return parsed
