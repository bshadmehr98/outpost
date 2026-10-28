"""The command line. One function per command, in the order you would use them.

  outpost init            make a platform folder to fill in
  outpost needs           show what will be fetched
  outpost fetch           download everything into box/        (needs internet)
  outpost pack            turn box/ into signed tar.xz files   (for GitHub releases or USB)
  outpost gitea setup     install Gitea on its server          (needs SSH)
  outpost upload          push the box into Gitea              (needs SSH)
  outpost infra setup X   configure the nodes of infrastructure X
  outpost infra verify X  prove the nodes use Gitea and nothing else
  outpost check           run the smoke checks on an infrastructure
  outpost demo push       push the demo project to Gitea; Actions builds and deploys it on the sealed nodes
  outpost egress          seal or open a server's outbound internet
  outpost proxy setup     the egress proxy: named exceptions to the seal (api.twilio.com...), if servers.yml has one
  outpost proxy verify    prove it lets the allowed names through and refuses the rest
  outpost ui              the same, as a local web page
  outpost lab up|down     throwaway servers in Docker, to see a full run without any VPS

Day two:
  outpost hosts           what every server runs right now (versions, seal, workloads)
  outpost gitea backup    archive Gitea's data to backups/; `gitea restore <file>` puts it back
  outpost infra upgrade X OS upgrade on the nodes (apt, apk or dnf), from Gitea
  outpost pki rotate      new CA and certificate, pushed everywhere
  outpost report          one HTML page with the evidence, for an auditor or a boss
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import __version__, box as boxmod, hosts as hostsmod, pki, report as reportmod, versions
from .config import ConfigError, load_mirrors, load_servers
from .http import HttpError
from .infra import TYPES
from .infra.common import NodeContext
from .infra import egress, runner, verify as verifymod
from .needs import add_platform_files, load_needs
from .paths import Platform
from . import ssh
from .ssh import Host, SshError

TEMPLATE = Path(__file__).parent / "templates" / "platform"


def log(msg: str = "") -> None:
    print(msg, flush=True)


# -- helpers -------------------------------------------------------------------

def _platform(args) -> Platform:
    platform = Platform(args.dir)
    if not platform.exists() and args.command != "init":
        raise ConfigError(f"No servers.yml in {platform.root}. Run `outpost init` here, or pass --dir.")
    return platform


def _servers(platform: Platform):
    servers = load_servers(platform.servers_file)
    ssh.use_config(servers.ssh_config)
    versions.use_arch(servers.arch)
    return servers


def _ca_pem(platform: Platform) -> str:
    ca = platform.pki_dir / "ca.crt"
    if not ca.exists():
        raise RuntimeError("pki/ca.crt is missing. Run `outpost gitea setup` first; it creates the CA.")
    return ca.read_text()


def _cached_facts(platform: Platform, alias: str) -> dict | None:
    """What `outpost hosts` last learned about a server (state/hosts/<alias>.json), without asking it."""
    return hostsmod.cached(platform).get(alias)


def _proxy_info(platform: Platform, servers, gitea=None, make_host=Host):
    """How nodes reach the egress proxy, or None when servers.yml has no egress_proxy. On the Gitea host its
    address is Gitea's; elsewhere the address recorded by `proxy setup`, else the cached facts, else asked over SSH."""
    cfg = servers.egress_proxy
    if not cfg:
        return None
    from .gitea.connect import gitea_info
    from .infra import proxy as proxymod
    gitea = gitea or gitea_info(platform, servers)
    ip = None
    if cfg.host != servers.gitea.host:
        state = platform.read_state("proxy") or {}
        ip = (state.get("ip") if state.get("host") == cfg.host else None) \
            or (_cached_facts(platform, cfg.host) or {}).get("ip") or make_host(cfg.host).ip()
    return proxymod.info(cfg, gitea, servers.gitea.host, ip)


def _add_proxy_package(platform: Platform, servers, needs) -> None:
    """With an egress proxy, tinyproxy joins the apt or rpm needs of the proxy host's distro."""
    from .infra import proxy as proxymod
    cfg = servers.egress_proxy
    if cfg:
        proxymod.add_package(needs, cfg, None if cfg.distro else _cached_facts(platform, cfg.host))


def _node_context(platform: Platform, servers, infra, hosts: list[Host]) -> NodeContext:
    from .gitea.connect import gitea_info
    info = gitea_info(platform, servers)
    uploaded = platform.read_state("upload") or {}
    node_ips = [h.ip() for h in hosts] if len(hosts) > 1 else []
    proxy = _proxy_info(platform, servers, info)
    # a proxy on its own host is one more address every node may reach; on the Gitea host it already is
    proxy_ip = [proxy.ip] if proxy and not proxy.on_gitea else []
    return NodeContext(
        gitea=info,
        ca_pem=_ca_pem(platform),
        distros=uploaded.get("distros", []),
        apk_branches=uploaded.get("apk_branches", []),
        rpm_distros=uploaded.get("rpm_distros", []),
        allow=list(dict.fromkeys(infra.allow + node_ips + proxy_ip)),
        infra_name=infra.name,
        args=infra.args,
        proxy=proxy,
    )


def _proxy_info_or_warn(platform: Platform, servers, info):
    """For the config image: the proxy settings when they can be known; a warning instead of a failure."""
    try:
        return _proxy_info(platform, servers, info)
    except (RuntimeError, SshError) as e:
        log(f"  note: the egress proxy's address is unknown ({e}); the config image is built without proxy.env")
        return None


def _print_results(node: str, results: list[dict]) -> bool:
    ok_all = all(r["ok"] for r in results)
    log(f"  {node}: {'all good' if ok_all else 'PROBLEMS'}")
    for r in results:
        mark = "ok  " if r["ok"] else "FAIL"
        detail = f"  ({r['detail'][:90]})" if r["detail"] and not r["ok"] else ""
        log(f"    {mark} {r['name']}{detail}")
    return ok_all


# -- commands ------------------------------------------------------------------

def cmd_init(args) -> int:
    target = Path(args.folder or args.dir).resolve()
    if (target / "servers.yml").exists():
        log(f"{target} already has a servers.yml; nothing done.")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for item in TEMPLATE.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        elif not dest.exists():
            shutil.copy2(item, dest)
    log(f"Created a platform folder in {target}")
    log("Next: edit servers.yml and needs/, then run `outpost needs` to see what will be fetched.")
    return 0


def cmd_needs(args) -> int:
    platform = _platform(args)
    needs = load_needs(platform.needs_dir)
    mirrors = load_mirrors(platform.mirrors_file)
    servers = _servers(platform)
    add_platform_files(needs, versions.platform_files(mirrors, servers.infra_types))
    _add_proxy_package(platform, servers, needs)
    log(f"Needs from {', '.join(needs.sources) or 'no needs files'}:")
    for ref in needs.images:
        log(f"  image  {ref}")
    for python, specs in needs.pypi.items():
        log(f"  pypi   (python {python}) {len(specs)} specs: {', '.join(specs[:6])}{' ...' if len(specs) > 6 else ''}")
    if needs.npm:
        log(f"  npm    {len(needs.npm)} packages: {', '.join(f'{n.name}@{n.version}' for n in needs.npm[:6])}{' ...' if len(needs.npm) > 6 else ''}")
    for distro, pkgs in needs.apt.items():
        log(f"  apt    ({distro}) {', '.join(pkgs)}")
    for branch, pkgs in needs.apk.items():
        log(f"  apk    ({branch}) {', '.join(pkgs)}")
    for distro, pkgs in needs.rpm.items():
        log(f"  rpm    ({distro}) {', '.join(pkgs)}")
    for f in needs.files:
        log(f"  file   {f.name} {f.version}  <- {f.url}")
    if servers.egress_proxy:
        log(f"  (tinyproxy is for the egress proxy on {servers.egress_proxy.host}, from servers.yml egress_proxy)")
    log(f"Total: {json.dumps(needs.summary())}")
    return 0


def cmd_fetch(args) -> int:
    from .fetch import fetch_all
    platform = _platform(args)
    needs = load_needs(platform.needs_dir)
    mirrors = load_mirrors(platform.mirrors_file)
    servers = _servers(platform)
    add_platform_files(needs, versions.platform_files(mirrors, servers.infra_types))
    _add_proxy_package(platform, servers, needs)
    results = fetch_all(needs, platform.box_dir, mirrors, only=set(args.only) if args.only else None, log=log)
    platform.write_state("fetch", results)
    failures = [f"{k}: {v['error']}" for k, v in results.items() if isinstance(v, dict) and v.get("error")]
    failures += [f for k, v in results.items() if isinstance(v, dict) for f in v.get("failed", [])]
    log()
    log(f"Box: {results['manifest']['files']} files, {results['manifest']['bytes'] / 1e6:.0f} MB in {platform.box_dir}")
    if failures:
        log("Some things could not be fetched:")
        for f in failures:
            log(f"  - {f}")
        return 1
    return 0


def cmd_pack(args) -> int:
    platform = _platform(args)
    pub, key = pki.ensure_signing_key(platform.pki_dir, platform.secrets_dir)
    made = boxmod.pack(platform.box_dir, Path(args.out), signer=lambda m: pki.sign(m, key))
    for path in made:
        log(f"  {path}  ({path.stat().st_size / 1e6:.0f} MB)")
    log(f"Signed with secrets/box.key; verify with pki/box.pub. Upload with: outpost upload {args.out}/*.tar.xz")
    return 0


def cmd_gitea(args) -> int:
    platform = _platform(args)
    servers = _servers(platform)
    if args.action == "setup":
        from .gitea.setup import setup
        info = setup(platform, servers, log=log)
        log()
        log(f"Gitea {info['version']} is up at https://{info['name']} ({info['ip']})")
        log(f"  web login: {servers.gitea.user} / secrets/gitea-admin-password")
        log("Next: outpost upload")
        return 0
    if args.action == "backup":
        from .gitea.backup import backup
        backup(platform, servers, Path(args.out), keep_on_server=args.keep_on_server, log=log)
        return 0
    if args.action == "restore":
        from .gitea.backup import restore
        if not args.archive:
            raise RuntimeError("gitea restore needs the archive: outpost gitea restore backups/gitea-....tar.gz")
        restore(platform, servers, Path(args.archive), log=log)
        return 0
    state = platform.read_state("gitea")
    if not state:
        log("Gitea has not been set up yet. Run: outpost gitea setup")
        return 1
    for k, v in state.items():
        log(f"  {k}: {v}")
    log(f"  egress: {egress.status(Host(servers.gitea.host))}")
    return 0


def cmd_upload(args) -> int:
    from .gitea.connect import connect, gitea_info
    from .gitea.upload import upload_box
    platform = _platform(args)
    servers = _servers(platform)
    sources = [Path(s) for s in args.source] or [platform.box_dir]
    for src in sources:
        if not src.exists():
            raise RuntimeError(f"{src} does not exist. Run `outpost fetch` first, or pass the box folder or tar.xz files.")
    box, keep = boxmod.open_box(sources[0] if len(sources) == 1 and sources[0].is_dir() else sources)
    try:
        sig, pub = box / "manifest.sig", platform.pki_dir / "box.pub"
        if sig.exists() and pub.exists():
            if not pki.verify(box / "manifest.json", sig, pub):
                raise RuntimeError("manifest.sig does not match pki/box.pub. The box was altered or signed with another key.")
            log("Signature OK")
        info = gitea_info(platform, servers)
        proxy = _proxy_info_or_warn(platform, servers, info)
        with connect(platform, servers) as client:
            stats = upload_box(box, client, info, _ca_pem(platform), log=log, proxy=proxy)
        config = stats.get("config_image", {})
        platform.write_state("upload", {"distros": config.get("distros", []), "apk_branches": config.get("apk_branches", []),
                                        "rpm_distros": config.get("rpm_distros", []), "stats": stats, "source": [str(s) for s in sources]})
    finally:
        if keep:
            keep.cleanup()
    log("Next: outpost infra setup <name>")
    return 0


def cmd_infra(args) -> int:
    platform = _platform(args)
    servers = _servers(platform)
    if args.action == "list":
        for name, infra in servers.infrastructures.items():
            state = platform.read_state(f"verify-{name}") or {}
            log(f"  {name:12} {infra.type:8} nodes: {', '.join(infra.nodes)}   last verify: {state.get('result', 'never')}")
        return 0
    infra = servers.infra(args.name)
    hosts = [Host(n) for n in infra.nodes]
    if args.action == "upgrade":
        return _infra_upgrade(platform, servers, infra, hosts)
    return _infra_setup(platform, servers, infra, hosts, setup=args.action == "setup")


def _infra_setup(platform: Platform, servers, infra, hosts: list[Host], setup: bool) -> int:
    ctx = _node_context(platform, servers, infra, hosts)
    module = TYPES[infra.type]
    if setup:
        log(f"Setting up {infra.name} ({infra.type}) on {', '.join(infra.nodes)}")
        result = module.setup(ctx, hosts, log=log)
        result["runner"] = _install_runner(platform, servers, ctx, hosts[0])
        platform.write_state(f"infra-{infra.name}", {"type": infra.type, **result})
        log()
    log(f"==> verifying {infra.name}")
    all_ok = True
    report = {}
    for host in hosts:
        results = verifymod.verify_node(host, ctx, module.extra_checks(ctx) + runner.extra_checks())
        report[host.alias] = results
        all_ok &= _print_results(host.alias, results)
        hostsmod.record(platform, host.alias, hostsmod.inspect(host))
    platform.write_state(f"verify-{infra.name}", {"result": "ok" if all_ok else "problems", "nodes": report})
    if all_ok and setup:
        log(f"Next: outpost check --infra {infra.name}   (smoke check)   or   outpost demo push   (deploy the demo through Gitea Actions)")
    return 0 if all_ok else 1


UPGRADE_SCRIPT = """. /etc/os-release 2>/dev/null
case " $ID $ID_LIKE " in
  *alpine*)
    apk update 2>&1 | tail -3
    apk upgrade 2>&1 | tail -25
    ;;
  *rhel*|*fedora*|*centos*|*rocky*|*almalinux*)
    dnf makecache -q 2>&1 | tail -3
    dnf upgrade -y -q 2>&1 | tail -25
    if command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; then echo 'REBOOT-REQUIRED'; fi
    ;;
  *)
    apt-get update -qq 2>&1 | tail -3
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q -o Dpkg::Options::=--force-confold 2>&1 | grep -E '^[0-9]+ upgraded|^Setting up|^E:|^W:|Unpacking' | tail -25
    test -f /var/run/reboot-required && echo 'REBOOT-REQUIRED' || true
    ;;
esac
"""


def _infra_upgrade(platform: Platform, servers, infra, hosts: list[Host]) -> int:
    """OS upgrade on every node, answered by Gitea only: apt-get on Debian and Ubuntu, apk on Alpine, dnf on
    the RHEL family, decided on the node from /etc/os-release. Put the packages to keep current in a needs
    file (needs/os-updates.yml), fetch, upload, then run this; the seal stays on throughout."""
    failed = []
    for host in hosts:
        log(f"==> {host.alias}: package index update and upgrade from Gitea")
        out = host.run(UPGRADE_SCRIPT, timeout=3600)
        for line in out.stdout.strip().splitlines():
            log(f"  [{host.alias}] {line}")
        if not out.ok or any(marker in out.stdout for marker in ("E:", "ERROR:", "Error:")):   # apt, apk, dnf
            failed.append(host.alias)
            log(f"  [{host.alias}] FAILED: {out.stderr.strip()[-300:]}")
        elif "REBOOT-REQUIRED" in out.stdout:
            log(f"  [{host.alias}] a reboot is required for the new kernel or libc to take effect")
        hostsmod.record(platform, host.alias, hostsmod.inspect(host))
    if failed:
        log(f"upgrade failed on {', '.join(failed)}")
        return 1
    log("done; the nodes stayed sealed throughout")
    return 0


def _install_runner(platform: Platform, servers, ctx: NodeContext, host: Host) -> dict:
    """A Gitea Actions runner on the first node, so `outpost demo push` (or your own workflows) can deploy here."""
    from .gitea.connect import connect
    with connect(platform, servers) as client:
        token = client.runner_registration_token()
        known = {r["name"] for r in client.runners()}
    return runner.install(host, ctx, runner.labels_for(servers, host.alias), token, known, log=log)


def cmd_demo(args) -> int:
    from . import demo
    platform = _platform(args)
    servers = _servers(platform)
    if args.action == "push":
        demo.push(platform, servers, log=log)
        return 0
    result = demo.status(platform, servers)
    log(f"Workflow runs of {result['repo']}  ({result['actions_url']})")
    for run in result["runs"] or [{"sha": "-", "status": "no runs yet", "conclusion": "", "jobs": [], "when": ""}]:
        log(f"  {run['sha']:10} {run['status']:12} {run['conclusion'] or '':10} {run['when'] or ''}")
        for job in run["jobs"]:
            log(f"      {job['name']:32} {job['status']:12} {job['conclusion'] or ''}")
    log("Serving:")
    for name, s in result["serving"].items():
        h = s["health"]
        if not h:
            log(f"  {name:10} nothing on {s['node']}:{s['port']} yet")
            continue
        sealed = "sealed at build and now" if h.get("sealed_at_build") and h.get("sealed_now") else "NOT SEALED"
        log(f"  {name:10} {s['url']}  commit {h.get('sha', '')[:10]}  {sealed}")
    return 0


def cmd_check(args) -> int:
    from .checks import load_checks, run_check
    from .gitea.connect import gitea_info
    platform = _platform(args)
    servers = _servers(platform)
    infra = servers.infra(args.infra)
    info = gitea_info(platform, servers)
    checks = [c for c in load_checks(platform.checks_dir) if c.applies_to(infra.type) and (not args.name or c.name == args.name)]
    if not checks:
        log(f"No checks for {infra.type} in {platform.checks_dir}" + (f" named {args.name}" if args.name else ""))
        return 1
    host = Host(infra.nodes[0])
    env = {"GITEA": info.name, "ORG": info.org, **_proxy_check_env(platform, servers)}
    results = []
    for check in checks:
        if "proxy" in check.requires and "PROXY_URL" not in env:
            log(f"check {check.name}: skipped, it needs the egress proxy and none is set up "
                "(egress_proxy in servers.yml, then `outpost proxy setup`)")
            continue
        log(f"check {check.name} on {host.alias}: {check.description}")
        results.append(run_check(check, host, env, log=log))
    platform.write_state(f"check-{infra.name}", {"results": results})
    failed = [r["name"] for r in results if not r["ok"]]
    log()
    log(f"{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def _proxy_check_env(platform: Platform, servers) -> dict[str, str]:
    """PROXY_URL, PROXY_ALLOWED (a name the proxy lets through) and PROXY_REFUSED (one it must refuse) for the
    smoke checks, from state/proxy.json; empty when no proxy is configured or it was never set up."""
    from .infra import proxy as proxymod
    state = platform.read_state("proxy") or {}
    if not servers.egress_proxy or not state.get("url") or not state.get("allow"):
        return {}
    return {"PROXY_URL": state["url"], "PROXY_ALLOWED": proxymod.probe_name(state["allow"]),
            "PROXY_REFUSED": proxymod.refused_name(state["allow"])}


def cmd_proxy(args) -> int:
    """The egress proxy: install and seal it on its host, bring already set-up nodes up to date, verify."""
    from .gitea.connect import gitea_info
    from .infra import k3s, proxy as proxymod
    from .infra.common import apply_proxy
    platform = _platform(args)
    servers = _servers(platform)
    cfg = servers.egress_proxy
    if not cfg:
        raise ConfigError("servers.yml has no egress_proxy section. Add one (allow: [api.twilio.com]) to give sealed "
                          "nodes named exceptions; see docs/architecture/proxy.md.")
    info = gitea_info(platform, servers)
    host = Host(cfg.host)
    on_gitea = cfg.host == servers.gitea.host
    proxy = proxymod.info(cfg, info, servers.gitea.host, None if on_gitea else host.ip())
    # what the proxy host itself may reach when sealed: Gitea's own list on the Gitea host; elsewhere that of
    # the infrastructure it belongs to, if any, plus its fellow nodes
    member = next((i for i in servers.infrastructures.values() if cfg.host in i.nodes), None)
    egress_allow = list(servers.gitea.allow) if on_gitea else list(member.allow) if member else []
    client_allow = list(dict.fromkeys(servers.gitea.allow + [a for i in servers.infrastructures.values() for a in i.allow]))
    uploaded = platform.read_state("upload") or {}
    ctx = NodeContext(gitea=info, ca_pem=_ca_pem(platform), distros=uploaded.get("distros", []),
                      apk_branches=uploaded.get("apk_branches", []), rpm_distros=uploaded.get("rpm_distros", []),
                      allow=egress_allow, infra_name="egress proxy", proxy=proxy)
    if args.action == "setup":
        log(f"Setting up the egress proxy on {cfg.host}: {proxy.url}, allowing {', '.join(cfg.allow)}")
        result = proxymod.setup(host, ctx, cfg, egress_allow=egress_allow, client_allow=client_allow, log=log)
        platform.write_state("proxy", {**result, "when": time.strftime("%Y-%m-%dT%H:%M:%S")})
        done = [n for n in servers.infrastructures if platform.read_state(f"infra-{n}")]
        if done:
            log(f"==> nodes of {', '.join(done)}: proxy settings (proxy.env, profile, Docker client config)")
        for name in done:
            infra = servers.infra(name)
            hosts = [Host(n) for n in infra.nodes]
            node_ctx = _node_context(platform, servers, infra, hosts)
            for node in hosts:
                apply_proxy(node, node_ctx, log=log)
            if infra.type == "k3s":
                k3s.apply_configmap(hosts[0], node_ctx.proxy, log=log)
        log()
    log(f"==> verifying the egress proxy on {cfg.host}")
    results = proxymod.verify(host, ctx, cfg)
    ok = _print_results(cfg.host, results)
    state = platform.read_state("proxy") or {}
    platform.write_state("proxy", {**state, "verify": {"result": "ok" if ok else "problems", "checks": results,
                                                         "when": time.strftime("%Y-%m-%dT%H:%M:%S")}})
    hostsmod.record(platform, cfg.host, hostsmod.inspect(host))
    if ok and args.action == "setup":
        log(f"Programs opt in with HTTPS_PROXY={proxy.url} (see /etc/outpost/proxy.env on every node); "
            "those that do not still have no internet. Next: outpost check hello-proxy --infra <name>")
    return 0 if ok else 1


def cmd_egress(args) -> int:
    platform = _platform(args)
    servers = _servers(platform)
    if args.target == "gitea":
        aliases = [servers.gitea.host]
    elif args.target in servers.infrastructures:
        aliases = servers.infrastructures[args.target].nodes
    else:
        aliases = [args.target]
    for alias in aliases:
        host = Host(alias)
        if args.action == "status":
            log(f"  {alias}: {egress.status(host)}")
        elif args.action == "seal":
            log(f"  {alias}: {egress.seal(host, args.reason or '')}")
        else:
            log(f"  {alias}: {egress.open_(host, args.reason or '')}  (remember: outpost egress {args.target} seal)")
    return 0


def cmd_lab(args) -> int:
    from . import lab
    platform = _platform(args)
    if args.action == "up":
        lab.up(platform, k3s=not args.small, log=log)
        log("Next: outpost gitea setup, outpost upload, outpost infra setup dev, outpost check --infra dev")
    else:
        lab.down(platform, log=log)
    return 0


def cmd_ui(args) -> int:
    from .ui.server import serve
    platform = _platform(args)
    return serve(platform, args.port)


def cmd_hosts(args) -> int:
    platform = _platform(args)
    servers = _servers(platform)
    if args.refresh or not hostsmod.cached(platform):
        log("Asking every server (one SSH round trip each)")
        data = hostsmod.refresh(platform, servers, log=log)
    else:
        data = hostsmod.cached(platform)
        for alias, h in data.items():
            if h.get("reachable"):
                inst = ", ".join(f"{k} {v}" for k, v in h["installed"].items() if v) or "nothing installed yet"
                log(f"  {alias:14} {h.get('pretty', '?'):24} {h.get('ip', '?'):15} {h.get('egress', '?'):14} {inst}   (seen {h.get('seen', '?')})")
            else:
                log(f"  {alias:14} unreachable: {h.get('error', '')}   (seen {h.get('seen', '?')})")
        log("From state/hosts/. `outpost hosts --refresh` asks the servers again.")
    drift = {a: hostsmod.drift(h) for a, h in data.items() if hostsmod.drift(h)}
    for alias, items in drift.items():
        log(f"  {alias}: not at the pinned version: {', '.join(items)}; run `outpost infra setup` (or `gitea setup`) to bring it up to date")
    return 0


def cmd_report(args) -> int:
    platform = _platform(args)
    try:
        servers = _servers(platform)
    except ConfigError:
        servers = None
    out = reportmod.write(platform, servers, Path(args.out) if args.out else None)
    v = reportmod.verdict(reportmod.build(platform, servers))
    log(f"wrote {out}")
    log("verdict: " + ("sealed and verified" if v["ok"] else "not fully proven; open the page for why"))
    return 0


def cmd_pki(args) -> int:
    """Rotate the CA: new CA and Gitea certificate, then push them everywhere the old one went."""
    import shutil
    import time
    platform = _platform(args)
    servers = _servers(platform)
    if not platform.read_state("gitea"):
        raise RuntimeError("Gitea has not been set up; there is nothing to rotate. Run `outpost gitea setup`.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old_pki, old_secrets = platform.pki_dir / "archive" / stamp, platform.secrets_dir / "archive" / stamp
    old_pki.mkdir(parents=True, exist_ok=True)
    old_secrets.mkdir(parents=True, exist_ok=True)
    for name, folder, archive in (("ca.crt", platform.pki_dir, old_pki), ("gitea.crt", platform.pki_dir, old_pki),
                                  ("ca.key", platform.secrets_dir, old_secrets), ("gitea.key", platform.secrets_dir, old_secrets)):
        if (folder / name).exists():
            shutil.move(folder / name, archive / name)
    log(f"==> [1/3] old CA and certificate moved to pki/archive/{stamp} and secrets/archive/{stamp}; making new ones and installing them on Gitea")
    from .gitea.setup import setup
    setup(platform, servers, log=log)
    log("==> [2/3] config image with the new CA")
    from .gitea.connect import connect, gitea_info
    from .gitea.upload import push_config_image
    uploaded = platform.read_state("upload") or {}
    with connect(platform, servers) as client:
        info = gitea_info(platform, servers)
        push_config_image(client, info, _ca_pem(platform), uploaded.get("distros", []),
                          apk_branches=uploaded.get("apk_branches", []), rpm_distros=uploaded.get("rpm_distros", []), log=log,
                          proxy=_proxy_info_or_warn(platform, servers, info))
    done = [n for n in servers.infrastructures if platform.read_state(f"infra-{n}")]
    log(f"==> [3/3] nodes of {', '.join(done) or 'no infrastructure yet'}: trust the new CA")
    rc = 0
    for name in done:
        infra = servers.infra(name)
        hosts = [Host(n) for n in infra.nodes]
        rc |= _infra_setup(platform, servers, infra, hosts, setup=True)
        for host in hosts:   # Docker and containerd read the CA file when they next connect; the runner needs a restart
            host.run("systemctl try-restart outpost-runner 2>/dev/null || true")
    log("rotation done. Images built before today still work; rebuild anything that baked the old pki/ca.crt into itself.")
    return rc


# -- parser --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="outpost", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=".", help="platform folder (default: current directory)")
    p.add_argument("--version", action="version", version=f"outpost {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="create a platform folder")
    i.add_argument("folder", nargs="?", help="where to create it (default: current directory)")
    i.set_defaults(func=cmd_init)
    sub.add_parser("needs", help="show what will be fetched").set_defaults(func=cmd_needs)

    f = sub.add_parser("fetch", help="download everything into box/ (needs internet)")
    f.add_argument("--only", nargs="*", choices=boxmod.KINDS, help="fetch only these kinds")
    f.set_defaults(func=cmd_fetch)

    k = sub.add_parser("pack", help="turn box/ into signed tar.xz files")
    k.add_argument("--out", default="dist", help="output folder (default: dist)")
    k.set_defaults(func=cmd_pack)

    g = sub.add_parser("gitea", help="install, inspect, back up or restore the Gitea server")
    g.add_argument("action", choices=["setup", "info", "backup", "restore"])
    g.add_argument("archive", nargs="?", help="restore: the backups/*.tar.gz to restore")
    g.add_argument("--out", default="backups", help="backup: folder for the archive (default: backups)")
    g.add_argument("--keep-on-server", action="store_true", help="backup: also leave the archive in /var/tmp/outpost-backups on the server")
    g.set_defaults(func=cmd_gitea)

    u = sub.add_parser("upload", help="push the box into Gitea")
    u.add_argument("source", nargs="*", help="box folder or tar.xz files (default: box/)")
    u.set_defaults(func=cmd_upload)

    i = sub.add_parser("infra", help="set up, verify or upgrade an infrastructure")
    i.add_argument("action", choices=["setup", "verify", "upgrade", "list"])
    i.add_argument("name", nargs="?")
    i.set_defaults(func=cmd_infra)

    c = sub.add_parser("check", help="run smoke checks on an infrastructure")
    c.add_argument("name", nargs="?", help="only this check")
    c.add_argument("--infra", required=True)
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("demo", help="push the demo project; Gitea Actions builds and deploys it on the sealed nodes")
    d.add_argument("action", choices=["push", "status"])
    d.set_defaults(func=cmd_demo)

    e = sub.add_parser("egress", help="seal or open outbound internet on a server")
    e.add_argument("target", help="gitea, an infrastructure name, or an SSH alias")
    e.add_argument("action", choices=["seal", "open", "status"])
    e.add_argument("--reason", help="written to the server's egress log")
    e.set_defaults(func=cmd_egress)

    x = sub.add_parser("proxy", help="the egress proxy: named exceptions to the seal (servers.yml egress_proxy)")
    x.add_argument("action", choices=["setup", "verify"])
    x.set_defaults(func=cmd_proxy)

    w = sub.add_parser("ui", help="open the local dashboard")
    w.add_argument("--port", type=int, default=8484)
    w.set_defaults(func=cmd_ui)

    lb = sub.add_parser("lab", help="start or remove throwaway servers in Docker")
    lb.add_argument("action", choices=["up", "down"])
    lb.add_argument("--small", action="store_true", help="no k3s server (saves about 1 GB of RAM)")
    lb.set_defaults(func=cmd_lab)

    h = sub.add_parser("hosts", help="what every server runs right now: versions, seal, workloads")
    h.add_argument("--refresh", action="store_true", help="ask the servers again instead of showing the cached answers")
    h.set_defaults(func=cmd_hosts)

    r = sub.add_parser("report", help="write the proof report as one HTML page")
    r.add_argument("--out", help="where to write it (default: state/report.html)")
    r.set_defaults(func=cmd_report)

    k2 = sub.add_parser("pki", help="rotate the CA and the Gitea certificate")
    k2.add_argument("action", choices=["rotate"])
    k2.set_defaults(func=cmd_pki)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "infra" and args.action != "list" and not args.name:
        print("error: infra setup/verify needs an infrastructure name", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (ConfigError, RuntimeError, SshError, HttpError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
