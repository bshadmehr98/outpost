"""The dashboard: one HTML page plus a few JSON endpoints, served on localhost only.

  GET  /                        the page
  GET  /api/overview            servers, needs, box, state, checks, lab and step status, as JSON
  GET  /api/file?name=...       read servers.yml, mirrors.yml, needs/*.yml or checks/*.yml
  PUT  /api/file?name=...       write one of those files (must stay valid YAML)
  DELETE /api/file?name=...     remove a needs file
  POST /api/servers             write servers.yml from a form {gitea: {...}, infrastructures: {...}, egress_proxy: {...}}
  POST /api/ssh-check           {"alias": "dev-1"} -> can we reach it, are we root
  POST /api/project/scan        {"folder": "~/code/my-api"} -> lockfiles, images, apt/apk/rpm packages found
  POST /api/project/save        a proposal -> needs/<name>.yml
  PUT  /api/lockfile?project=&filename=   upload one lockfile into needs/<project>/
  GET  /api/search?q=flask      find a package or image in the needs and in the box
  GET  /api/runs                past runs; GET /api/runs/<id> one log
  POST /api/server-status       {"alias": "dev-1"} -> egress state and the egress log of that server
  POST /api/run                 start an outpost command; body {"args": ["fetch"]}
  GET  /api/run/<id>            its log so far, its "==> " steps, whether it is still running, and a translation of the failure
  GET  /api/hosts               the cached facts of every server (state/hosts/); POST refreshes them over SSH
  GET  /api/backups             the archives in backups/, for the restore picker
  GET  /report.html             the proof report, built from state/ only

Commands run as subprocesses of this same tool, so the page can never do anything
the CLI cannot. Logs land in state/runs/.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .. import demo, ssh, versions
from .. import hosts as hostsmod
from .. import report as reportmod
from ..checks import load_checks
from ..config import ConfigError, load_mirrors, load_servers
from ..http import HttpError
from ..needs import Needs, add_pinned_files, add_proxy_package, load_needs, read_needs_file
from ..ssh import SshError
from ..system import AirGappedSystem
from ..workspace import Workspace
from . import explain, projects, status

ALLOWED_COMMANDS = {
    "needs",
    "fetch",
    "pack",
    "gitea",
    "upload",
    "infra",
    "check",
    "egress",
    "proxy",
    "lab",
    "demo",
    "hosts",
    "report",
    "pki",
}
RUNS: dict[str, dict] = {}


# -- what the page shows -------------------------------------------------------


def ssh_aliases() -> list[str]:
    """Host aliases from ~/.ssh/config, for the dropdowns. Wildcards are left out."""
    config = Path.home() / ".ssh" / "config"
    if not config.exists():
        return []
    aliases = []
    for m in re.finditer(r"^\s*Host\s+(.+)$", config.read_text(errors="replace"), re.M):
        aliases += [a for a in m.group(1).split() if not any(c in a for c in "*?!")]
    return aliases


def lab_status() -> dict:
    from .. import lab

    if not shutil.which("docker"):
        return {"docker": False, "running": []}
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=^lab-"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.split()
    except (subprocess.SubprocessError, OSError):
        out = []
    return {"docker": True, "running": sorted(n for n in out if n in lab.SERVERS)}


def needs_files(workspace: Workspace) -> list[dict]:
    """One entry per needs/*.yml: what it declares and whether it reads cleanly."""
    out = []
    for path in sorted(workspace.needs_dir.glob("*.yml")) + sorted(workspace.needs_dir.glob("*.yaml")):
        entry = {"file": f"needs/{path.name}", "name": path.stem, "summary": None, "error": None, "raw": None}
        try:
            raw = yaml.safe_load(path.read_text()) or {}
            entry["raw"] = raw if isinstance(raw, dict) else None
            entry["name"] = str(raw.get("name", path.stem)) if isinstance(raw, dict) else path.stem
            n = Needs()
            read_needs_file(path, n)
            entry["summary"] = n.summary()
        except (ConfigError, yaml.YAMLError) as e:
            entry["error"] = str(e)
        out.append(entry)
    return out


def needs_detail(workspace: Workspace, infra_types: set[str], servers=None) -> dict:
    """The merged list `outpost fetch` will work from, including Gitea, Docker and k3s themselves, and
    tinyproxy when servers.yml has an egress proxy (left out while its distro is unknown)."""
    needs = load_needs(workspace.needs_dir)
    add_pinned_files(needs, versions.pinned_files(load_mirrors(workspace.mirrors_file), infra_types))
    if servers and servers.egress_proxy:
        with contextlib.suppress(ConfigError):
            add_proxy_package(needs, servers.egress_proxy, hostsmod.cached(workspace).get(servers.egress_proxy.host))
    return {
        "images": needs.images,
        "pypi": {py: specs for py, specs in needs.pypi.items()},
        "npm": [f"{n.name}@{n.version}" for n in needs.npm],
        "apt": needs.apt,
        "apk": needs.apk,
        "rpm": needs.rpm,
        "files": [{"name": f.name, "version": f.version} for f in needs.files],
        "summary": needs.summary(),
    }


def overview(workspace: Workspace) -> dict:
    data: dict = {"root": str(workspace.root), "servers": None, "error": None}
    infra_types: set[str] = set()
    servers = None
    try:
        servers = load_servers(workspace.servers_file)
        versions.use_arch(servers.arch)
        infra_types = servers.infra_types
        data["servers"] = {
            "gitea": vars(servers.gitea),
            "infrastructures": {n: vars(i) for n, i in servers.infrastructures.items()},
            "ssh_config": str(servers.ssh_config) if servers.ssh_config else None,
            "is_lab": servers.gitea.host == "lab-gitea",
            "arch": servers.arch,
            "egress_proxy": vars(servers.egress_proxy) if servers.egress_proxy else None,
        }
    except ConfigError as e:
        data["error"] = str(e)
    needs = None
    try:
        needs = load_needs(workspace.needs_dir)
        data["needs"] = needs.summary()
        data["needs_detail"] = needs_detail(workspace, infra_types, servers)
    except ConfigError as e:
        data["needs"] = {"error": str(e)}
        data["needs_detail"] = None
    full = load_needs(workspace.needs_dir) if needs else Needs()
    if needs:
        add_pinned_files(full, versions.pinned_files(load_mirrors(workspace.mirrors_file), infra_types))
    data["box_status"] = status.box_status(workspace, full)
    data["box_changes"] = status.box_changes(workspace, full)
    data["sync"] = status.sync(workspace, data["box_status"], list((data["servers"] or {}).get("infrastructures", {})))
    data["preflight"] = status.preflight(workspace)
    data["runs"] = status.runs(workspace, limit=8, live=_live())
    data["needs_files"] = needs_files(workspace)
    manifest = workspace.box_dir / "manifest.json"
    data["box"] = json.loads(manifest.read_text()) if manifest.exists() else None
    if data["box"]:
        data["box"].pop("items", None)
    data["state"] = (
        {p.stem: json.loads(p.read_text()) for p in sorted(workspace.state_dir.glob("*.json"))}
        if workspace.state_dir.exists()
        else {}
    )
    data["files"] = (
        ["servers.yml", "mirrors.yml"]
        + [f"needs/{p.name}" for p in sorted(workspace.needs_dir.glob("*.yml"))]
        + [f"checks/{p.name}" for p in sorted(workspace.checks_dir.glob("*.yml"))]
    )
    try:
        data["checks"] = [
            {"name": c.name, "description": c.description, "infra": c.infra, "requires": c.requires}
            for c in load_checks(workspace.checks_dir)
        ]
    except ConfigError as e:
        data["checks"] = [{"name": "error", "description": str(e), "infra": []}]
    data["secrets"] = {"gitea-token": (workspace.secrets_dir / "gitea-token").exists()}
    data["ssh_aliases"] = ssh_aliases()
    data["lab"] = lab_status()
    data["versions"] = hostsmod.expected_versions()
    data["running"] = [{"id": rid, "args": r["args"]} for rid, r in RUNS.items() if r["proc"].poll() is None]
    data["demo"] = {
        "local": (workspace.root / "demo").exists(),
        "urls": demo.demo_urls(AirGappedSystem(workspace, servers)) if servers else {},
        "files": sorted(demo.project_files(workspace.root / "demo")) if (workspace.root / "demo").exists() else [],
        "status": workspace.read_state("demo-status"),
    }
    data["hosts"] = hostsmod.cached(workspace)
    data["roles"] = hostsmod.roles(servers) if servers else {}
    data["drift"] = {a: hostsmod.drift(h) for a, h in data["hosts"].items() if hostsmod.drift(h)}
    data["backups"] = list_backups(workspace)
    data["report"] = reportmod.verdict(reportmod.build(workspace, servers))
    return data


def list_backups(workspace: Workspace) -> list[dict]:
    folder = workspace.root / "backups"
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.tar.gz"), reverse=True):
        st = p.stat()
        out.append(
            {
                "file": str(p.relative_to(workspace.root)),
                "bytes": st.st_size,
                "when": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            }
        )
    return out


def refresh_hosts(workspace: Workspace, aliases: list[str] | None) -> dict:
    """Ask the servers again (one SSH round trip each) and cache the answers."""
    servers = load_servers(workspace.servers_file)
    ssh.use_config(servers.ssh_config)
    wanted = [a for a in (aliases or []) if re.fullmatch(r"[A-Za-z0-9._-]+", a)] or None
    return hostsmod.refresh(
        workspace, servers, wanted, log=lambda *_: None, make_host=lambda a: ssh.Host(a, connect_timeout=8)
    )


# -- writing files -------------------------------------------------------------


def safe_file(workspace: Workspace, name: str) -> Path:
    path = (workspace.root / name).resolve()
    allowed = (
        path == workspace.servers_file
        or path == workspace.mirrors_file
        or (path.parent in (workspace.needs_dir, workspace.checks_dir) and path.suffix in (".yml", ".yaml"))
    )
    if not allowed or ".." in name:
        raise PermissionError(f"not an editable file: {name}")
    return path


def write_servers(workspace: Workspace, form: dict) -> None:
    """servers.yml from the form on the page. Validated by reading it back."""
    g = form.get("gitea") or {}
    data: dict = {}
    if form.get("ssh_config"):
        data["ssh_config"] = str(form["ssh_config"])
    gitea = {"host": str(g.get("host", "")).strip(), "name": str(g.get("name", "")).strip().lower()}
    if g.get("ip"):
        gitea["ip"] = str(g["ip"]).strip()
    data["gitea"] = gitea
    data["infrastructures"] = {}
    for name, spec in (form.get("infrastructures") or {}).items():
        key = re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")
        if not key:
            continue
        nodes = spec.get("nodes") or []
        if isinstance(nodes, str):
            nodes = [n for n in re.split(r"[,\s]+", nodes) if n]
        entry = {"type": str(spec.get("type", "compose")), "nodes": nodes}
        if spec.get("args"):
            entry["args"] = str(spec["args"])
        if spec.get("allow"):
            entry["allow"] = spec["allow"]
        data["infrastructures"][key] = entry
    if "egress_proxy" in form:
        proxy = _proxy_form(form.get("egress_proxy"))
    else:  # a form without the card keeps what servers.yml has
        try:
            current = yaml.safe_load(workspace.servers_file.read_text()) if workspace.servers_file.exists() else None
        except yaml.YAMLError:
            current = None
        proxy = current.get("egress_proxy") if isinstance(current, dict) else None
    if proxy:
        data["egress_proxy"] = proxy
    text = (
        "# Which servers play which role. Hosts are aliases from your ~/.ssh/config.\n"
        "# The SSH user must be root or have passwordless sudo. Written by the Outpost dashboard.\n"
        + yaml.safe_dump(data, sort_keys=False, default_flow_style=None)
    )
    tmp = workspace.servers_file.with_suffix(".yml.tmp")
    tmp.write_text(text)
    try:
        load_servers(tmp)
    except ConfigError:
        tmp.unlink()
        raise
    tmp.replace(workspace.servers_file)


def _proxy_form(p) -> dict | None:
    """The egress_proxy card: allow is a textarea (names separated by lines, commas or spaces); an empty
    allow list means no proxy. Host, port and distro only when they differ from the defaults."""
    if not isinstance(p, dict):
        return None
    allow = p.get("allow") or []
    if isinstance(allow, str):
        allow = [a for a in re.split(r"[,\s]+", allow) if a]
    if not allow:
        return None
    out: dict = {}
    if str(p.get("host") or "").strip():
        out["host"] = str(p["host"]).strip()
    port = str(p.get("port") or "").strip()
    if port and port != "3128":
        out["port"] = int(port) if port.isdigit() else port  # load_servers explains a bad one
    out["allow"] = [str(a).strip() for a in allow]
    if str(p.get("distro") or "").strip():
        out["distro"] = str(p["distro"]).strip()
    return out


def _choose_ssh_config(workspace: Workspace) -> str | None:
    """Pick the ssh config file the way the CLI does, and say what is wrong instead of raising.
    A servers.yml that does not load yet still lets OUTPOST_SSH_CONFIG count."""
    try:
        path = load_servers(workspace.servers_file).ssh_config
    except ConfigError:
        path = None
    try:
        ssh.use_config(path)
    except SshError as e:
        return str(e)
    return None


def ssh_check(workspace: Workspace, alias: str) -> dict:
    """Reach a host the way Outpost will: ssh, then become root. Says what is missing in plain words."""
    from ..ssh import Host, SshError
    from ..ssh.options import ssh_options

    if not re.fullmatch(r"[A-Za-z0-9._-]+", alias or ""):
        return {"ok": False, "message": "that is not an SSH alias"}
    problem = _choose_ssh_config(workspace)
    if problem:
        return {"ok": False, "message": problem}
    plain = subprocess.run(["ssh", *ssh_options(8), alias, "id -un"], capture_output=True, text=True)
    if plain.returncode != 0:
        return {"ok": False, "message": f"ssh {alias} failed: {plain.stderr.strip() or 'no answer'}"}
    user = plain.stdout.strip()
    try:
        facts = Host(alias, connect_timeout=8).facts()
    except SshError as e:
        return {
            "ok": False,
            "user": user,
            "message": f"connected as {user}, but could not become root: {str(e).splitlines()[-1]}",
        }
    return {
        "ok": True,
        "user": user,
        "facts": facts,
        "message": f"connected as {user}; {facts.get('id', '?')} {facts.get('codename', '')} {facts.get('arch', '')} at {facts.get('ip', '?')}",
    }


def server_status(workspace: Workspace, alias: str) -> dict:
    """Egress state and the egress log of one server, read over SSH."""
    from ..infra import egress
    from ..ssh import Host, SshError

    if not re.fullmatch(r"[A-Za-z0-9._-]+", alias or ""):
        return {"ok": False, "message": "that is not an SSH alias"}
    problem = _choose_ssh_config(workspace)
    if problem:
        return {"ok": False, "message": problem}
    host = Host(alias, connect_timeout=8)
    try:
        state = egress.status(host)
        log = host.run("tail -n 40 /var/log/outpost-egress.log 2>/dev/null || true").stdout
        facts = (
            host.run("uptime -p 2>/dev/null; df -h / | awk 'NR==2{print $4\" free of \"$2}'")
            .stdout.strip()
            .splitlines()
        )
    except SshError as e:
        return {"ok": False, "message": str(e).splitlines()[-1]}
    return {
        "ok": True,
        "egress": state,
        "log": log,
        "uptime": facts[0] if facts else "",
        "disk": facts[1] if len(facts) > 1 else "",
    }


def save_lockfile(workspace: Workspace, project: str, filename: str, body: bytes) -> dict:
    project = re.sub(r"[^a-z0-9._-]+", "-", project.lower()).strip("-.")
    filename = Path(filename).name
    if not project or not filename or filename.startswith("."):
        raise PermissionError("the upload needs a project name and a file name")
    folder = workspace.needs_dir / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(body)
    return projects.describe_lockfile(path)


# -- running commands ----------------------------------------------------------


def start_run(workspace: Workspace, args: list[str]) -> str:
    if not args or args[0] not in ALLOWED_COMMANDS:
        raise PermissionError(f"command not allowed: {' '.join(args)}")
    if any(r["proc"].poll() is None for r in RUNS.values()):
        raise PermissionError("another command is still running; wait for it to finish")
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + args[0]
    logs = workspace.state_dir / "runs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / f"{run_id}.log"
    cmd = [sys.executable, "-m", "outpost", "--dir", str(workspace.root), *args]
    env = dict(os.environ)  # make the package importable even when running from a source checkout
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    with open(log_file, "w") as out:
        out.write("$ outpost " + " ".join(args) + "\n")
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=workspace.root, env=env)
    RUNS[run_id] = {"proc": proc, "log": log_file, "args": args, "started": time.time()}

    def trailer():  # so the history knows how it ended
        code = proc.wait()
        with open(log_file, "a") as out:
            out.write(f"\n[outpost exit {code} after {int(time.time() - RUNS[run_id]['started'])}s]\n")

    threading.Thread(target=trailer, daemon=True).start()
    return run_id


def _live() -> set[str]:
    return {rid for rid, r in RUNS.items() if r["proc"].poll() is None}


def run_status(run_id: str) -> dict:
    run = RUNS.get(run_id)
    if not run:
        raise KeyError(run_id)
    code = run["proc"].poll()
    log = run["log"].read_text()
    return {
        "id": run_id,
        "args": run["args"],
        "running": code is None,
        "exit": code,
        "seconds": int(time.time() - run["started"]),
        "log": log[-30000:],
        "steps": status.steps(log),
        "explain": explain.explain(log) if code not in (None, 0) else None,
    }


# -- HTTP ----------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    workspace: Workspace = None  # pyright: ignore[reportAssignmentType]  set by serve()

    def log_message(self, *_):  # keep the terminal quiet
        pass

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self, key: str) -> str:
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def _body_bytes(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _body(self) -> dict:
        return json.loads(self._body_bytes() or b"{}")

    def do_GET(self):  # noqa: C901 too complex; split into named steps in the refactor
        path = urlparse(self.path).path
        try:
            if path == "/":
                body = (Path(__file__).parent / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/overview":
                self._json(overview(self.workspace))
            elif path == "/api/hosts":
                self._json(hostsmod.cached(self.workspace))
            elif path == "/api/backups":
                self._json(list_backups(self.workspace))
            elif path == "/report.html":
                try:
                    servers = load_servers(self.workspace.servers_file)
                except ConfigError:
                    servers = None
                body = reportmod.render(reportmod.build(self.workspace, servers)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/file":
                name = self._query("name")
                file = safe_file(self.workspace, name)
                self._json({"name": name, "content": file.read_text() if file.exists() else ""})
            elif path.startswith("/api/run/"):
                self._json(run_status(path.rsplit("/", 1)[1]))
            elif path == "/api/search":
                needs = load_needs(self.workspace.needs_dir)
                self._json(status.search(self.workspace, needs, self._query("q")))
            elif path == "/api/runs":
                self._json(status.runs(self.workspace, live=_live()))
            elif path.startswith("/api/runs/"):
                run_id = path.rsplit("/", 1)[1]
                if not re.fullmatch(r"[0-9-]+-[a-z]+", run_id):
                    raise PermissionError("not a run id")
                log = self.workspace.state_dir / "runs" / f"{run_id}.log"
                text = log.read_text(errors="replace") if log.exists() else ""
                code = re.search(r"\[outpost exit (\d+)", text[-200:])
                self._json(
                    {
                        "id": run_id,
                        "log": text[-60000:],
                        "steps": status.steps(text),
                        "explain": explain.explain(text) if code and code.group(1) != "0" else None,
                    }
                )
            else:
                self._json({"error": "not found"}, 404)
        except (PermissionError, KeyError, ConfigError) as e:
            self._json({"error": str(e)}, 400)

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/file":
                name = self._query("name")
                file = safe_file(self.workspace, name)
                content = self._body().get("content", "")
                yaml.safe_load(content)  # must at least be YAML
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
                self._json({"saved": name})
            elif path == "/api/lockfile":
                self._json(
                    save_lockfile(self.workspace, self._query("project"), self._query("filename"), self._body_bytes())
                )
            else:
                self._json({"error": "not found"}, 404)
        except (PermissionError, yaml.YAMLError, ConfigError) as e:
            self._json({"error": str(e)}, 400)

    def do_DELETE(self):
        try:
            name = self._query("name")
            file = safe_file(self.workspace, name)
            if file.parent != self.workspace.needs_dir:
                raise PermissionError("only needs files can be deleted here")
            file.unlink(missing_ok=True)
            self._json({"deleted": name})
        except PermissionError as e:
            self._json({"error": str(e)}, 400)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/run":
                self._json({"id": start_run(self.workspace, self._body().get("args", []))})
            elif path == "/api/servers":
                write_servers(self.workspace, self._body())
                self._json({"saved": "servers.yml"})
            elif path == "/api/ssh-check":
                self._json(ssh_check(self.workspace, self._body().get("alias", "")))
            elif path == "/api/server-status":
                self._json(server_status(self.workspace, self._body().get("alias", "")))
            elif path == "/api/hosts":
                self._json(refresh_hosts(self.workspace, self._body().get("aliases")))
            elif path == "/api/demo":
                self._json(demo.status(AirGappedSystem.load(self.workspace.root)))
            elif path == "/api/project/scan":
                try:  # the Gitea name and org let a literal gitea.internal/pkgs/... FROM be read back into its need
                    g = load_servers(self.workspace.servers_file).gitea
                    gitea = f"{g.name}/{g.org}"
                except (ConfigError, yaml.YAMLError):
                    gitea = None
                self._json(projects.scan(self._body().get("folder", ""), gitea))
            elif path == "/api/project/save":
                saved = projects.write_needs(self.workspace, self._body())
                self._json({"saved": f"needs/{saved.name}"})
            else:
                self._json({"error": "not found"}, 404)
        except (PermissionError, ConfigError, ValueError, RuntimeError, HttpError, SshError) as e:
            self._json({"error": str(e)}, 400)


def serve(workspace: Workspace, port: int = 8484, open_browser: bool = True) -> int:
    Handler.workspace = workspace
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Outpost dashboard for {workspace.root}\n  {url}\nCtrl-C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
