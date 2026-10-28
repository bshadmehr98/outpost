"""render_html(): the report page, one function per section.

Each section returns its lines of HTML; render_html joins them. Every value from state/ is escaped.
"""

from html import escape

from outpost.hosts import version_drift
from outpost.report.style import CSS
from outpost.report.verdict import verdict


def render_html(data: dict) -> str:
    """The whole page, from report_data()."""
    lines = [
        *_head_and_title(data),
        *_verdict_box(verdict(data)),
        *_what_this_workspace_is(data),
        *_servers(data),
        *_named_exceptions(data),
    ]
    for name, infra in data["infras"].items():
        lines += _infrastructure(name, infra, data)
    lines += [_HOW_TO_READ, "</body></html>"]
    return "\n".join(lines)


# -- the top of the page -----------------------------------------------------------------


def _head_and_title(data: dict) -> list[str]:
    return [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Outpost proof report</title><style>{CSS}</style></head><body>",
        f"<h1>Outpost proof report</h1><p class='sub'>Workspace <code>{escape(data['root'])}</code>, generated {escape(data['generated'])} by outpost {escape(data['outpost'])}.</p>",
    ]


def _verdict_box(v: dict) -> list[str]:
    if v["ok"]:
        infrastructures = "infrastructure" + ("s" if len(v["verified"]) != 1 else "")
        return [
            f"<div class='verdict ok'><b>Sealed and verified.</b> {len(v['sealed'])} of {v['servers']} servers report their internet switched off; "
            f"{len(v['verified'])} {infrastructures} passed every check.</div>"
        ]
    return [
        f"<div class='verdict bad'><b>Not fully proven.</b> {escape('; '.join(_why_not_proven(v)) or 'see below')}.</div>"
    ]


def _why_not_proven(v: dict) -> list[str]:
    why = []
    if not v["verified"]:
        why.append("no infrastructure has been verified yet")
    if v["open"]:
        why.append("internet is open on " + ", ".join(v["open"]))
    if v["problems"]:
        why.append("verification problems on " + ", ".join(v["problems"]))
    if v["leaks"]:
        why.append("a deployment reports internet access: " + ", ".join(v["leaks"]))
    if v["unreachable"]:
        why.append("not reachable when last inspected: " + ", ".join(v["unreachable"]))
    return why


# -- what the system is, and its servers ----------------------------------------------------


def _what_this_workspace_is(data: dict) -> list[str]:
    gitea = data["gitea"]
    lines = [
        "<h2>What this workspace is</h2><table>",
        f"<tr><th>Gitea</th><td><code>https://{escape(gitea.get('name', '?'))}</code> on <code>{escape(str(gitea.get('host', '?')))}</code> ({escape(str(gitea.get('ip', '?')))}), version {escape(str(gitea.get('version', 'not installed')))}</td></tr>",
        f"<tr><th>Architecture</th><td>{escape(data['arch'])}</td></tr>",
    ]
    if data["upload"]:
        lines.append(_box_in_gitea(data["upload"]))
    backup = data["backup"]
    last_backup = (
        escape(backup["when"]) + ", " + str(round(backup["bytes"] / 1e6)) + " MB" if backup else "none recorded"
    )
    lines.append(f"<tr><th>Last backup</th><td>{last_backup}</td></tr>")
    pinned = ", ".join(f"{name} {escape(version)}" for name, version in data["pinned"].items())
    lines.append(f"<tr><th>Pinned versions</th><td>{pinned}</td></tr></table>")
    return lines


def _box_in_gitea(upload: dict) -> str:
    """One row: how many files Gitea has from the box, of which kinds, and for which distros."""
    kinds = {kind: tally for kind, tally in (upload.get("stats") or {}).items() if _is_tally(tally)}
    total = sum(int(tally.get("uploaded", 0)) + int(tally.get("exists", 0)) for tally in kinds.values())

    def listed(key: str) -> str:
        return ", ".join(upload.get(key) or []) or "none"

    return (
        f"<tr><th>Box in Gitea</th><td>{total} files across {', '.join(kinds) or 'no kinds'}; "
        f"apt distros: {listed('distros')}; apk branches: {listed('apk_branches')}; rpm distros: {listed('rpm_distros')}</td></tr>"
    )


def _is_tally(value) -> bool:
    return isinstance(value, dict) and "uploaded" in value


def _servers(data: dict) -> list[str]:
    lines = [
        "<h2>Servers</h2><table><tr><th>Server</th><th>Role</th><th>System</th><th>Internet</th><th>Installed</th><th>Last inspected</th></tr>"
    ]
    for alias, roles in data["roles"].items():
        lines.append(_server_row(alias, ", ".join(roles), data["hosts"].get(alias) or {}))
    lines.append("</table>")
    return lines


def _server_row(alias: str, roles: str, facts: dict) -> str:
    server, role = f"<td><code>{escape(alias)}</code>", f"<td>{escape(roles)}</td>"
    if not facts:
        return f"<tr>{server}</td>{role}<td colspan=4 class='muted'>never inspected</td></tr>"
    if not facts.get("reachable"):
        return (
            f"<tr>{server}</td>{role}<td colspan=3 class='fail'>unreachable: {escape(facts.get('error', ''))}</td>"
            f"<td>{escape(facts.get('seen', ''))}</td></tr>"
        )
    sealed = str(facts.get("egress", "")).startswith("sealed")
    internet = "switched off" if sealed else escape(str(facts.get("egress", "")))
    at_boot = "yes" if facts.get("egress_boot") == "enabled" else "no"
    installed = ", ".join(f"{k} {v}" for k, v in (facts.get("installed") or {}).items() if v) or "nothing yet"
    drift = version_drift(facts)
    drift_note = f"<div class=small><span class=fail>drift:</span> {escape('; '.join(drift))}</div>" if drift else ""
    return (
        f"<tr>{server}<div class='small muted'>{escape(facts.get('ip', ''))}</div></td>{role}"
        f"<td>{escape(facts.get('pretty', ''))}<div class='small muted'>{escape(facts.get('arch', ''))}, up {escape(facts.get('uptime', '?'))}, disk {escape(facts.get('disk_free', '?'))} free</div></td>"
        f"<td class='{'pass' if sealed else 'fail'}'>{internet}<div class='small muted'>at boot: {at_boot}</div></td>"
        f"<td>{escape(installed)}{drift_note}</td><td>{escape(facts.get('seen', ''))}</td></tr>"
    )


# -- the egress proxy -----------------------------------------------------------------------


def _named_exceptions(data: dict) -> list[str]:
    """The egress proxy: the only names a sealed workload can reach, and only if it asks for them."""
    proxy = data.get("proxy") or {}
    config, state = proxy.get("config"), proxy.get("state") or {}
    if not config:
        return [
            "<h2>Named exceptions</h2>",
            "<p>None. There is no egress proxy (no <code>egress_proxy</code> in servers.yml), so no workload can reach any "
            "name outside the workspace.</p>",
        ]
    lines = ["<h2>Named exceptions</h2>", "<table>", _allowed_names(config), _proxy_host(config, state)]
    lines.append(
        f"<tr><th>Set up</th><td>{escape(state['when']) if state.get('when') else '<span class=fail>not yet (outpost proxy setup)</span>'}</td></tr>"
    )
    if state.get("verify"):
        lines.append(_proxy_verified(state["verify"]))
    if state.get("allow") and sorted(state["allow"]) != sorted(config.get("allow") or []):
        lines.append(
            "<tr><th>Note</th><td class='fail'>servers.yml allows other names than the proxy was set up with; run <code>outpost proxy setup</code></td></tr>"
        )
    lines += [_proxy_log((data["hosts"].get(config.get("host")) or {}).get("proxy_log_tail") or []), "</table>"]
    return lines


def _allowed_names(config: dict) -> str:
    names = ", ".join(f"<code>{escape(name)}</code>" for name in config.get("allow") or [])
    return (
        f"<tr><th>Allowed names</th><td>{names}"
        "<div class='small muted'>Reached only through the egress proxy, by programs that set HTTPS_PROXY; every other name is refused "
        "with 403, and a program without the proxy variables still has no internet.</div></td></tr>"
    )


def _proxy_host(config: dict, state: dict) -> str:
    where = f"<code>{escape(str(config.get('host')))}</code>"
    if state.get("url"):
        where += f" ({escape(str(state.get('ip')))}), <code>{escape(str(state.get('url')))}</code>"
    return (
        f"<tr><th>Proxy host</th><td>{where}<div class='small muted'>tinyproxy {escape(str(state.get('tinyproxy') or '?'))} from Gitea, "
        "as user outpost-proxy, the only user whose outbound TCP 443 and 80 the seal lets through, on that host only</div></td></tr>"
    )


def _proxy_verified(verification: dict) -> str:
    passed = verification.get("result") == "ok"
    failed = [check["name"] for check in verification.get("checks") or [] if not check.get("ok")]
    outcome = "all checks passed" if passed else "problems: " + escape(", ".join(failed))
    return (
        f"<tr><th>Verified</th><td class='{'pass' if passed else 'fail'}'>{escape(verification.get('when', ''))}: "
        f"{outcome}</td></tr>"
    )


def _proxy_log(tail: list[str]) -> str:
    shown = (
        f"<pre class='small'>{escape(chr(10).join(tail))}</pre>"
        if tail
        else "<span class='muted'>no lines recorded</span>"
    )
    return (
        f"<tr><th>Proxy log</th><td>{shown}"
        "<div class='small muted'>The last lines of /var/log/tinyproxy/tinyproxy.log when the host was last inspected: every CONNECT with its name, and every refusal.</div></td></tr>"
    )


# -- one infrastructure ------------------------------------------------------------------------


def _infrastructure(name: str, infra: dict, data: dict) -> list[str]:
    """Its heading, the verification table, the smoke checks and the deployed demo."""
    lines = [
        f"<h2>{escape(name)} <span class='muted small'>({escape(infra['type'])}, nodes {escape(', '.join(infra['nodes']))})</span></h2>"
    ]
    lines += _verification_table(infra) if infra["verify"] else ["<p class='muted'>Not verified yet.</p>"]
    if infra["checks"]:
        lines += _smoke_checks(infra)
    serving = data["serving"].get(name)
    if serving and serving.get("health"):
        lines.append(_deployed_application(serving, data["demo_at"]))
    return lines


def _verification_table(infra: dict) -> list[str]:
    """One row per verification, one column per node."""
    verification = infra["verify"]
    nodes = verification.get("nodes") or {}
    passed = verification.get("result") == "ok"
    names = list(dict.fromkeys(result["name"] for results in nodes.values() for result in results))
    lines = [
        f"<h3>Verification, {escape(infra['verified_at'] or '')}: <span class='{'pass' if passed else 'fail'}'>{'all checks passed' if passed else 'problems'}</span></h3>",
        "<table><tr><th>Check</th>" + "".join(f"<th>{escape(node)}</th>" for node in nodes) + "</tr>",
    ]
    for name in names:
        cells = [
            _verification_cell(next((r for r in results if r["name"] == name), None)) for results in nodes.values()
        ]
        lines.append(f"<tr><td>{escape(name)}</td>{''.join(cells)}</tr>")
    lines.append("</table>")
    return lines


def _verification_cell(result: dict | None) -> str:
    if not result:
        return "<td></td>"
    if result["ok"]:
        return "<td class='pass'>ok</td>"
    return f"<td class='fail'>failed <span class='small muted'>{escape(result.get('detail', ''))}</span></td>"


def _smoke_checks(infra: dict) -> list[str]:
    lines = [
        f"<h3>Smoke checks, {escape(infra['checked_at'] or '')}</h3><table><tr><th>Check</th><th>Result</th><th>On</th></tr>"
    ]
    for check in infra["checks"]:
        lines.append(
            f"<tr><td><code>{escape(check['name'])}</code></td><td class='{'pass' if check['ok'] else 'fail'}'>{'passed' if check['ok'] else 'failed'}</td><td>{escape(check.get('node', ''))}</td></tr>"
        )
    lines.append("</table>")
    return lines


def _deployed_application(serving: dict, demo_at: str | None) -> str:
    """The demo measures the internet itself: when it was built, and on every request."""
    health = serving["health"]
    sealed = health.get("sealed_at_build") and health.get("sealed_now")
    return (
        f"<h3>Deployed application, {escape(demo_at or '')}</h3><p>Serving commit <code>{escape((health.get('sha') or '')[:12])}</code> at <code>{escape(serving.get('url', ''))}</code> on {escape(health.get('hostname', ''))}: "
        f"<span class='{'pass' if sealed else 'fail'}'>{'no internet at build time, no internet now' if sealed else 'INTERNET REACHABLE'}</span>.</p>"
    )


_HOW_TO_READ = (
    "<h2>How to read this</h2><p class='small'>Every server was configured by <code>outpost</code> over SSH to use one Gitea server for packages, images and git, "
    "and then sealed: an iptables rule set rejects outbound traffic to anything but Gitea, private networks and DNS, for the host and for its containers and pods, "
    "and a systemd unit restores it at boot. The verification table is the output of commands run on each node (<code>outpost infra verify</code>); "
    "the smoke checks build and run software from Gitea alone on a sealed node; the deployed application measures the internet itself at build time and on every request. "
    "Re-run any of them and regenerate this page to refresh the evidence.</p>"
)
