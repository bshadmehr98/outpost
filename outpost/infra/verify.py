"""Prove a node uses Gitea and nothing else. Each verification is one shell command that must succeed.

    verifications = node_verifications(ctx) + compose.verifications(ctx)    # [(name, command), ...]
    results = run_verifications(host, verifications)   # [{"name", "ok", "detail"}, ...]
    log_verifications(host.alias, results, log)          # True when all passed

The verifications that matter most:
  - the node reaches Gitea over TLS it trusts
  - the public internet is rejected, and the seal comes back after a reboot
  - every package client is configured to use Gitea, and only Gitea (apt, apk or dnf, whichever the node has)
With an egress proxy, infra/proxy adds its own (proxy_verifications): an allowed name gets through and
any other is refused.

All of them run as one script (verification_script). Each prints a line `CHECK|<name>|OK|<detail>`
or `CHECK|<name>|FAIL|<detail>`; parse_verifications reads them back. The names are read by the tests,
the report and the dashboard: keep them as they are.
"""

import shlex

from outpost import versions
from outpost.client_config import PATH_APK_REPOS, PATH_CA, PATH_RPM_REPO
from outpost.endpoints import GiteaEndpoint
from outpost.infra.context import NodeContext
from outpost.ssh import Host

MARKER = "CHECK"  # the first field of each result line; on the wire since the first release


def node_verifications(ctx: NodeContext) -> list[tuple[str, str]]:
    """The verifications every node runs, whatever its infrastructure type."""
    g = ctx.gitea
    # the tool's own exit status, not tail's: a source that fails to download fails the verification
    index_updates = by_family(
        apt='out=$(apt-get update -qq 2>&1); rc=$?; echo "$out" | tail -3; test $rc -eq 0',
        apk='out=$(apk update 2>&1); rc=$?; echo "$out" | tail -3; test $rc -eq 0',
        rpm='out=$(dnf makecache -q 2>&1); rc=$?; echo "$out" | tail -3; test $rc -eq 0',
    )
    return [
        ("gitea in /etc/hosts", f"getent hosts {g.name} | grep -q {g.ip}"),
        ("gitea name restored at boot", "systemctl is-enabled outpost-hosts.service"),
        (
            "gitea reachable with trusted TLS",
            f"curl --cacert {PATH_CA} -fsS --max-time 10 {g.url}/api/v1/version",
        ),
        ("system trust store has the CA", f"curl -fsS --max-time 10 {g.url}/api/v1/version"),
        (
            "pip points at gitea",
            f"grep -q {g.url} /etc/pip.conf && ! grep -rqs extra-index-url /etc/pip.conf /root/.pip /root/.config/pip",
        ),
        ("npm points at gitea", f"grep -q {g.url} /etc/npmrc"),
        ("package sources are gitea only", by_family(**gitea_only_sources_by_family(g))),
        ("node dns for containers running", "systemctl is-active outpost-dns"),
        ("package index updates offline", index_updates),
        ("egress sealed", 'test "$(/usr/local/sbin/outpost-egress status)" = sealed'),
        ("public internet rejected", "! curl --noproxy '*' -sS --max-time 5 https://pypi.org/simple/ >/dev/null 2>&1"),
        ("egress seal survives reboot", "systemctl is-enabled outpost-egress.service"),
        (
            "routed traffic sealed too (containers, pods)",
            "iptables -C FORWARD -j OUTPOST && iptables -t security -C FORWARD -j OUTPOST",
        ),
        ("egress script is the current one", "grep -q 'build_chain $ipt security DROP' /usr/local/sbin/outpost-egress"),
        (
            f"coredns at pinned version {versions.COREDNS}",
            f"/usr/local/bin/coredns -version | grep -q {versions.COREDNS}",
        ),
        (
            "pip.conf unchanged",
            f"test \"$(grep -c . /etc/pip.conf)\" = 3 && grep -q '^cert = {PATH_CA}' /etc/pip.conf",
        ),
    ]


def run_verifications(host: Host, verifications: list[tuple[str, str]], timeout: int = 600) -> list[dict]:
    """Run the verifications on host, in one ssh call.

    Returns:
        One {"name", "ok", "detail"} per verification. When the script printed nothing (ssh failed),
        a single failed "connect" result with the error.
    """
    result = host.run(verification_script(verifications), timeout=timeout)
    parsed = parse_verifications(result.stdout)
    if not parsed:
        parsed = [{"name": "connect", "ok": False, "detail": result.stderr.strip()[-300:] or "no output"}]
    return parsed


def log_verifications(node: str, results: list[dict], log=print) -> bool:
    """Log one node's verifications: a summary line, then one line each. Returns True when all passed."""
    all_passed = all(result["ok"] for result in results)
    log(f"  {node}: {'all good' if all_passed else 'PROBLEMS'}")
    for result in results:
        mark = "ok  " if result["ok"] else "FAIL"
        detail = f"  ({result['detail'][:90]})" if result["detail"] and not result["ok"] else ""
        log(f"    {mark} {result['name']}{detail}")
    return all_passed


# -- building blocks for the verifications -------------------------------------------------


def by_family(apt: str, apk: str, rpm: str) -> str:
    """One shell snippet that runs the right command for the node's family, read from /etc/os-release,
    so the same script (and the same verification names) works on Debian, Ubuntu, Alpine and the RHEL family.

    The case's exit status is that of the branch taken. ${ID_LIKE:-}: Debian and Alpine have no ID_LIKE,
    and Host.run turns on `set -u`, which stops on an unset variable.
    """
    return (
        '. /etc/os-release 2>/dev/null; case " ${ID:-} ${ID_LIKE:-} " in '
        f"*alpine*) {apk} ;; *rhel*|*fedora*|*centos*|*rocky*|*almalinux*) {rpm} ;; *) {apt} ;; esac"
    )


def gitea_only_sources_by_family(g: GiteaEndpoint) -> dict[str, str]:
    """For apt, apk and rpm: a command that passes when at least one source points at Gitea, and none
    points anywhere else.

    Both halves matter: from 0.1.0 the apt probe in setup used HEAD, which Gitea's Debian registry does
    not answer, so outpost.list was empty on every apt node, and a verification that only looked for
    foreign sources passed on an empty list.
    """

    def gitea_count(what: str, count: str, fix: str) -> str:
        return (
            f'{{ n=$({count}); if [ "$n" -gt 0 ]; then echo "{what} from gitea: $n"; '
            f"else echo {shlex.quote(f'no {what} point at gitea: {fix}')}; false; fi; }}"
        )

    fix = "is this release uploaded to Gitea? `outpost fetch` and `outpost upload` it, then `outpost infra setup` again"
    apt_lines = "grep -rhsE '^(deb |URIs:)' /etc/apt/sources.list /etc/apt/sources.list.d/"
    return dict(
        apt=(
            f"! grep -rhs '^deb ' /etc/apt/sources.list /etc/apt/sources.list.d/ | grep -v {g.url} && "
            f"! grep -rhs '^URIs:' /etc/apt/sources.list.d/ | grep -v {g.url} && "
            + gitea_count(
                "apt sources",
                f"{apt_lines} | grep -c {shlex.quote(g.apt_repo)}",
                f"/etc/apt/sources.list.d/outpost.list has no deb line; {fix}",
            )
        ),
        apk=(
            f"! grep -v '^#' {PATH_APK_REPOS} | grep -v '^$' | grep -v {g.url} && "
            + gitea_count(
                "apk repositories",
                f"grep -v '^#' {PATH_APK_REPOS} | grep -c {shlex.quote(g.apk_repo)}",
                f"{PATH_APK_REPOS} is empty; {fix}",
            )
        ),
        rpm=(
            "! grep -lsE '^enabled *= *1' /etc/yum.repos.d/*.repo | grep -v outpost.repo && "
            + gitea_count(
                "rpm repositories",
                f"grep -csE '^baseurl *= *{g.rpm_repo}/' {PATH_RPM_REPO}",
                f"{PATH_RPM_REPO} has no section; {fix}",
            )
        ),
    )


# -- the script and its output ------------------------------------------------------------


def verification_script(verifications: list[tuple[str, str]]) -> str:
    """One script that runs every verification, even after one fails (`set +e`), and prints one
    `CHECK|name|OK|detail` or `CHECK|name|FAIL|detail` line each; the detail is the last line of output."""
    lines = ["set +e"]
    for name, command in verifications:
        lines.append(
            f"out=$( ( {command} ) 2>&1 ); rc=$?; "
            f'if [ $rc -eq 0 ]; then echo "{MARKER}|{name}|OK|$(echo "$out" | tail -1)"; '
            f'else echo "{MARKER}|{name}|FAIL|$(echo "$out" | tail -1)"; fi'
        )
    return "\n".join(lines) + "\n"


def parse_verifications(output: str) -> list[dict]:
    """The {"name", "ok", "detail"} of each result line in the script's output; other lines are ignored."""
    results = []
    for line in output.splitlines():
        if line.startswith(f"{MARKER}|"):
            _, name, status, detail = line.split("|", 3)
            results.append({"name": name, "ok": status == "OK", "detail": detail.strip()})
    return results
