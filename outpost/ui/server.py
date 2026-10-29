"""The dashboard's HTTP server: one HTML page and a few JSON endpoints, on 127.0.0.1 only.

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
  POST /api/demo                ask Gitea and the nodes how the demo is doing
  GET  /api/backups             the archives in backups/, for the restore picker
  GET  /report.html             the proof report, built from state/ only

Only this laptop's own pages may change anything: a request other than GET must come from the
dashboard's own origin (no foreign Origin header), and a POST or a JSON PUT must say
`Content-Type: application/json`, which a plain HTML form or a "simple" cross-site request cannot.
Every request must name this laptop in its Host header, so a DNS-rebinding page cannot read the API.
"""

import json
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from outpost import demo
from outpost.config import ConfigError, load_servers
from outpost.hosts import cached_facts
from outpost.http import HttpError
from outpost.needs import load_needs
from outpost.report import render_html, report_data, servers_or_none
from outpost.ssh import SshError
from outpost.system import AirGappedSystem
from outpost.ui import projects
from outpost.ui.files import delete_needs_file, read_file, save_file, save_lockfile
from outpost.ui.overview import list_backups, overview
from outpost.ui.reach import reach_server, refresh_hosts, server_status
from outpost.ui.runs import live_runs, past_run, run_status, start_run
from outpost.ui.servers_form import write_servers
from outpost.ui.status import runs, search
from outpost.workspace import Workspace

PAGE = Path(__file__).parent / "index.html"
LOCAL_NAMES = {"127.0.0.1", "localhost", "[::1]"}  # what the Host header may name
JSON_TYPE = "application/json"
REQUEST_ERRORS = (PermissionError, KeyError, ValueError, RuntimeError, ConfigError, HttpError, SshError, yaml.YAMLError)


class Handler(BaseHTTPRequestHandler):
    """Answers the dashboard's requests from the route tables below. A request that fails with one of
    REQUEST_ERRORS gets {"error": message} and status 400."""

    workspace: Workspace = None  # pyright: ignore[reportAssignmentType]  set by serve()

    def log_message(self, *_):  # keep the terminal quiet
        pass

    def do_GET(self):
        self._answer(GET_ROUTES, GET_PREFIX_ROUTES)

    def do_PUT(self):
        self._answer(PUT_ROUTES)

    def do_POST(self):
        self._answer(POST_ROUTES)

    def do_DELETE(self):
        self._answer(DELETE_ROUTES)

    # -- what a route reads from the request ---------------------------------------------------
    def query(self, key: str) -> str:
        """One query parameter, or ""."""
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def body_bytes(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def body(self) -> dict:
        """The JSON body; {} when there is none."""
        return json.loads(self.body_bytes() or b"{}")

    # -- answering -------------------------------------------------------------------------------
    def send_json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), JSON_TYPE)

    def send_html(self, text: str) -> None:
        self._send(200, text.encode(), "text/html; charset=utf-8")

    def _answer(self, routes: dict, prefix_routes: dict | None = None) -> None:
        path = urlparse(self.path).path
        refused = self._refused(path)
        if refused:
            self.send_json({"error": refused}, 403)
            return
        route = routes.get(path) or _by_prefix(prefix_routes or {}, path)
        if not route:
            self.send_json({"error": "not found"}, 404)
            return
        try:
            route(self, path)
        except REQUEST_ERRORS as e:
            self.send_json({"error": str(e)}, 400)

    def _refused(self, path: str) -> str | None:
        """Why this request may not run, or None. See the module docstring."""
        host = self.headers.get("Host", "")
        if _host_name(host) not in LOCAL_NAMES:
            return f"the dashboard answers only to 127.0.0.1 or localhost, not {host or 'no Host header'}"
        if self.command == "GET":
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            return f"requests from {origin} may not change anything here"
        takes_json = self.command == "POST" or (self.command == "PUT" and path in JSON_PUT_PATHS)
        sends_json = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() == JSON_TYPE
        if takes_json and not sends_json:
            return f"send the body as {JSON_TYPE}"
        return None

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(workspace: Workspace, port: int = 8484, open_browser: bool = True) -> int:
    """Serve the dashboard on 127.0.0.1:port until Ctrl-C. Returns the exit code, 0."""
    handler = type("DashboardHandler", (Handler,), {"workspace": workspace})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
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


# -- the routes: (handler, path) -> None; each one sends its answer -----------------------------


def _page(h: Handler, path: str) -> None:
    h.send_html(PAGE.read_text())


def _report(h: Handler, path: str) -> None:
    h.send_html(render_html(report_data(h.workspace, servers_or_none(h.workspace))))


def _search(h: Handler, path: str) -> None:
    h.send_json(search(h.workspace, load_needs(h.workspace.needs_dir), h.query("q")))


def _project_scan(h: Handler, path: str) -> None:
    try:  # the Gitea name and org let a literal gitea.internal/pkgs/... FROM be read back into its need
        gitea_config = load_servers(h.workspace.servers_file).gitea
        gitea = f"{gitea_config.name}/{gitea_config.org}"
    except (ConfigError, yaml.YAMLError):
        gitea = None
    h.send_json(projects.scan(h.body().get("folder", ""), gitea))


def _project_save(h: Handler, path: str) -> None:
    h.send_json({"saved": f"needs/{projects.write_needs(h.workspace, h.body()).name}"})


def _servers(h: Handler, path: str) -> None:
    write_servers(h.workspace, h.body())
    h.send_json({"saved": "servers.yml"})


def _json_route(answer: Callable[[Handler], object]) -> Callable[[Handler, str], None]:
    """A route whose answer is one value, sent as JSON."""
    return lambda h, path: h.send_json(answer(h))


GET_ROUTES: dict[str, Callable[[Handler, str], None]] = {
    "/": _page,
    "/report.html": _report,
    "/api/overview": _json_route(lambda h: overview(h.workspace)),
    "/api/hosts": _json_route(lambda h: cached_facts(h.workspace)),
    "/api/backups": _json_route(lambda h: list_backups(h.workspace)),
    "/api/file": _json_route(lambda h: read_file(h.workspace, h.query("name"))),
    "/api/search": _search,
    "/api/runs": _json_route(lambda h: runs(h.workspace, live=live_runs())),
}
GET_PREFIX_ROUTES: dict[str, Callable[[Handler, str], None]] = {  # the rest of the path is a run id
    "/api/run/": lambda h, path: h.send_json(run_status(path.rsplit("/", 1)[1])),
    "/api/runs/": lambda h, path: h.send_json(past_run(h.workspace, path.rsplit("/", 1)[1])),
}
PUT_ROUTES: dict[str, Callable[[Handler, str], None]] = {
    "/api/file": _json_route(lambda h: save_file(h.workspace, h.query("name"), h.body().get("content", ""))),
    "/api/lockfile": _json_route(
        lambda h: save_lockfile(h.workspace, h.query("project"), h.query("filename"), h.body_bytes())
    ),
}
JSON_PUT_PATHS = {"/api/file"}  # /api/lockfile takes the file's own bytes
DELETE_ROUTES: dict[str, Callable[[Handler, str], None]] = {
    "/api/file": _json_route(lambda h: delete_needs_file(h.workspace, h.query("name"))),
}
POST_ROUTES: dict[str, Callable[[Handler, str], None]] = {
    "/api/run": _json_route(lambda h: {"id": start_run(h.workspace, h.body().get("args", []))}),
    "/api/servers": _servers,
    "/api/ssh-check": _json_route(lambda h: reach_server(h.workspace, h.body().get("alias", ""))),
    "/api/server-status": _json_route(lambda h: server_status(h.workspace, h.body().get("alias", ""))),
    "/api/hosts": _json_route(lambda h: refresh_hosts(h.workspace, h.body().get("aliases"))),
    "/api/demo": _json_route(lambda h: demo.status(AirGappedSystem.load(h.workspace.root))),
    "/api/project/scan": _project_scan,
    "/api/project/save": _project_save,
}


def _by_prefix(prefix_routes: dict, path: str):
    """The route whose prefix starts path; the longest prefix wins."""
    for prefix in sorted(prefix_routes, key=len, reverse=True):
        if path.startswith(prefix):
            return prefix_routes[prefix]
    return None


def _host_name(host: str) -> str:
    """The name in a Host header, without the port: "127.0.0.1:8484" -> "127.0.0.1", "[::1]:8484" -> "[::1]"."""
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host
