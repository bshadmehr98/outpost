"""The proof report: one standalone HTML page that says what the platform is, what was verified,
when, and what the servers are running. Built from the files in state/ only, so it needs no SSH
and shows exactly what the last commands recorded.

  outpost report [--out file.html]      write it (default state/report.html) and print the path
  the dashboard's /report.html          the same page, to print or save for an auditor
"""

import html
import time
from pathlib import Path

from . import __version__, hosts, versions
from .config import Servers
from .paths import Platform


def build(platform: Platform, servers: Servers | None) -> dict:
    state = {p.stem: platform.read_state(p.stem) for p in sorted(platform.state_dir.glob("*.json"))} if platform.state_dir.exists() else {}
    infras = {}
    if servers:
        for name, infra in servers.infrastructures.items():
            verify = state.get(f"verify-{name}") or {}
            checks = (state.get(f"check-{name}") or {}).get("results") or []
            infras[name] = {"type": infra.type, "nodes": infra.nodes, "verify": verify, "checks": checks,
                            "verified_at": _mtime(platform.state_dir / f"verify-{name}.json"),
                            "checked_at": _mtime(platform.state_dir / f"check-{name}.json"),
                            "set_up": bool(state.get(f"infra-{name}"))}
    demo = state.get("demo-status") or {}
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "outpost": __version__,
        "root": str(platform.root),
        "gitea": {**(state.get("gitea") or {}), "config": vars(servers.gitea) if servers else {}},
        "upload": state.get("upload") or {},
        "backup": state.get("backup") or {},
        "infras": infras,
        "hosts": hosts.cached(platform),
        "roles": hosts.roles(servers) if servers else {},
        "pinned": hosts.expected_versions(),
        "arch": servers.arch if servers else versions.ARCH,
        "serving": demo.get("serving") or {},
        "demo_at": _mtime(platform.state_dir / "demo-status.json"),
    }


def _mtime(path: Path) -> str | None:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)) if path.exists() else None


def verdict(data: dict) -> dict:
    """The one-line answer at the top: how many servers, how many verified sealed, anything wrong."""
    sealed = [a for a, h in data["hosts"].items() if h.get("reachable") and str(h.get("egress", "")).startswith("sealed")]
    open_ = [a for a, h in data["hosts"].items() if h.get("reachable") and not str(h.get("egress", "")).startswith("sealed")]
    unreachable = [a for a, h in data["hosts"].items() if not h.get("reachable")]
    verified = [n for n, i in data["infras"].items() if i["verify"].get("result") == "ok"]
    problems = [n for n, i in data["infras"].items() if i["verify"] and i["verify"].get("result") != "ok"]
    leaks = [n for n, s in data["serving"].items() if s.get("health") and not (s["health"].get("sealed_at_build") and s["health"].get("sealed_now"))]
    drift = {a: hosts.drift(h) for a, h in data["hosts"].items() if hosts.drift(h)}
    ok = not open_ and not problems and not leaks and bool(verified)
    return {"ok": ok, "sealed": sealed, "open": open_, "unreachable": unreachable, "verified": verified, "problems": problems,
            "leaks": leaks, "drift": drift, "servers": len(data["roles"])}


CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#15191e;max-width:960px;margin:40px auto;padding:0 20px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 10px;border-bottom:1px solid #ddd;padding-bottom:6px}h3{font-size:15px;margin:18px 0 6px}
.sub{color:#666;margin:0 0 18px}.verdict{padding:16px 20px;border-radius:10px;margin:18px 0;font-size:17px}
.ok{background:#e3f4ea;color:#14532d}.bad{background:#fbe7e8;color:#7f1d1d}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}th{text-align:left;color:#666;font-weight:600;padding:6px 8px;border-bottom:1px solid #ddd}
td{padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top}code{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:#f3f4f2;padding:0 4px;border-radius:4px}
.pass{color:#1a7f45;font-weight:600}.fail{color:#b4232c;font-weight:600}.muted{color:#777}.small{font-size:13px}
@media print{body{margin:0}h2{break-after:avoid}}
"""


def render(data: dict) -> str:
    v = verdict(data)
    e = html.escape
    g = data["gitea"]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Outpost proof report</title><style>{CSS}</style></head><body>"]
    out.append(f"<h1>Outpost proof report</h1><p class='sub'>Platform <code>{e(data['root'])}</code>, generated {e(data['generated'])} by outpost {e(data['outpost'])}.</p>")
    if v["ok"]:
        out.append(f"<div class='verdict ok'><b>Sealed and verified.</b> {len(v['sealed'])} of {v['servers']} servers report their internet switched off; "
                   f"{len(v['verified'])} infrastructure{'s' if len(v['verified']) != 1 else ''} passed every check.</div>")
    else:
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
        out.append(f"<div class='verdict bad'><b>Not fully proven.</b> {e('; '.join(why) or 'see below')}.</div>")

    out.append("<h2>What this platform is</h2><table>")
    out.append(f"<tr><th>Gitea</th><td><code>https://{e(g.get('name', '?'))}</code> on <code>{e(str(g.get('host', '?')))}</code> ({e(str(g.get('ip', '?')))}), version {e(str(g.get('version', 'not installed')))}</td></tr>")
    out.append(f"<tr><th>Architecture</th><td>{e(data['arch'])}</td></tr>")
    up = data["upload"]
    if up:
        stats = up.get("stats") or {}
        total = sum(int(x.get("uploaded", 0)) + int(x.get("exists", 0)) for x in stats.values() if isinstance(x, dict) and "uploaded" in x)
        out.append(f"<tr><th>Box in Gitea</th><td>{total} files across {', '.join(k for k, x in stats.items() if isinstance(x, dict) and 'uploaded' in x) or 'no kinds'}; apt distros: {', '.join(up.get('distros') or []) or 'none'}</td></tr>")
    bk = data["backup"]
    out.append(f"<tr><th>Last backup</th><td>{e(bk['when']) + ', ' + str(round(bk['bytes'] / 1e6)) + ' MB' if bk else 'none recorded'}</td></tr>")
    out.append("<tr><th>Pinned versions</th><td>" + ", ".join(f"{k} {e(v2)}" for k, v2 in data["pinned"].items()) + "</td></tr></table>")

    out.append("<h2>Servers</h2><table><tr><th>Server</th><th>Role</th><th>System</th><th>Internet</th><th>Installed</th><th>Last inspected</th></tr>")
    for alias, rs in data["roles"].items():
        h = data["hosts"].get(alias) or {}
        if not h:
            out.append(f"<tr><td><code>{e(alias)}</code></td><td>{e(', '.join(rs))}</td><td colspan=4 class='muted'>never inspected</td></tr>")
            continue
        if not h.get("reachable"):
            out.append(f"<tr><td><code>{e(alias)}</code></td><td>{e(', '.join(rs))}</td><td colspan=3 class='fail'>unreachable: {e(h.get('error', ''))}</td><td>{e(h.get('seen', ''))}</td></tr>")
            continue
        seal = str(h.get("egress", ""))
        inst = ", ".join(f"{k} {v2}" for k, v2 in (h.get("installed") or {}).items() if v2) or "nothing yet"
        drift = hosts.drift(h)
        out.append(f"<tr><td><code>{e(alias)}</code><div class='small muted'>{e(h.get('ip', ''))}</div></td><td>{e(', '.join(rs))}</td>"
                   f"<td>{e(h.get('pretty', ''))}<div class='small muted'>{e(h.get('arch', ''))}, up {e(h.get('uptime', '?'))}, disk {e(h.get('disk_free', '?'))} free</div></td>"
                   f"<td class='{'pass' if seal.startswith('sealed') else 'fail'}'>{'switched off' if seal.startswith('sealed') else e(seal)}<div class='small muted'>at boot: {'yes' if h.get('egress_boot') == 'enabled' else 'no'}</div></td>"
                   f"<td>{e(inst)}{('<div class=small><span class=fail>drift:</span> ' + e('; '.join(drift)) + '</div>') if drift else ''}</td><td>{e(h.get('seen', ''))}</td></tr>")
    out.append("</table>")

    for name, i in data["infras"].items():
        out.append(f"<h2>{e(name)} <span class='muted small'>({e(i['type'])}, nodes {e(', '.join(i['nodes']))})</span></h2>")
        ver = i["verify"]
        if not ver:
            out.append("<p class='muted'>Not verified yet.</p>")
        else:
            nodes = ver.get("nodes") or {}
            names = []
            for rs in nodes.values():
                for r in rs:
                    if r["name"] not in names:
                        names.append(r["name"])
            out.append(f"<h3>Verification, {e(i['verified_at'] or '')}: <span class='{'pass' if ver.get('result') == 'ok' else 'fail'}'>{'all checks passed' if ver.get('result') == 'ok' else 'problems'}</span></h3>")
            out.append("<table><tr><th>Check</th>" + "".join(f"<th>{e(n)}</th>" for n in nodes) + "</tr>")
            for c in names:
                cells = []
                for rs in nodes.values():
                    r = next((x for x in rs if x["name"] == c), None)
                    cells.append("<td></td>" if not r else "<td class='pass'>ok</td>" if r["ok"] else f"<td class='fail'>failed <span class='small muted'>{e(r.get('detail', ''))}</span></td>")
                out.append(f"<tr><td>{e(c)}</td>{''.join(cells)}</tr>")
            out.append("</table>")
        if i["checks"]:
            out.append(f"<h3>Smoke checks, {e(i['checked_at'] or '')}</h3><table><tr><th>Check</th><th>Result</th><th>On</th></tr>")
            for c in i["checks"]:
                out.append(f"<tr><td><code>{e(c['name'])}</code></td><td class='{'pass' if c['ok'] else 'fail'}'>{'passed' if c['ok'] else 'failed'}</td><td>{e(c.get('node', ''))}</td></tr>")
            out.append("</table>")
        s = data["serving"].get(name)
        if s and s.get("health"):
            h = s["health"]
            ok = h.get("sealed_at_build") and h.get("sealed_now")
            out.append(f"<h3>Deployed application, {e(data['demo_at'] or '')}</h3><p>Serving commit <code>{e((h.get('sha') or '')[:12])}</code> at <code>{e(s.get('url', ''))}</code> on {e(h.get('hostname', ''))}: "
                       f"<span class='{'pass' if ok else 'fail'}'>{'no internet at build time, no internet now' if ok else 'INTERNET REACHABLE'}</span>.</p>")

    out.append("<h2>How to read this</h2><p class='small'>Every server was configured by <code>outpost</code> over SSH to use one Gitea server for packages, images and git, "
               "and then sealed: an iptables rule set rejects outbound traffic to anything but Gitea, private networks and DNS, for the host and for its containers and pods, "
               "and a systemd unit restores it at boot. The verification table is the output of commands run on each node (<code>outpost infra verify</code>); "
               "the smoke checks build and run software from Gitea alone on a sealed node; the deployed application measures the internet itself at build time and on every request. "
               "Re-run any of them and regenerate this page to refresh the evidence.</p>")
    out.append("</body></html>")
    return "\n".join(out)


def write(platform: Platform, servers: Servers | None, out: Path | None = None) -> Path:
    out = out or platform.state_dir / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build(platform, servers)))
    return out
