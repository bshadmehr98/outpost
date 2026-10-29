import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from outpost.ui.server import Handler
from outpost.workspace import Workspace


@pytest.fixture
def ui(workspace_dir):
    handler = type("H", (Handler,), {"workspace": Workspace(workspace_dir)})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(url, method="GET", body=None, headers=None):
    """A request the way the page makes it: a JSON body says so in Content-Type."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_and_overview(ui):
    status, body = call(ui + "/")
    assert status == 200 and b"<title>Outpost</title>" in body
    status, body = call(ui + "/api/overview")
    data = json.loads(body)
    assert data["servers"]["gitea"]["name"] == "gitea.internal"
    assert data["needs"]["images"] == 2 and data["box"] is None
    assert "needs/demo.yml" in data["files"] and [c["name"] for c in data["checks"]] == [
        "hello-alpine",
        "hello-k3s",
        "hello-proxy-k3s",
        "hello-proxy",
        "hello-python",
    ]


def test_file_edit_is_restricted_and_validated(ui):
    status, body = call(ui + "/api/file?name=servers.yml")
    assert status == 200 and "gitea:" in json.loads(body)["content"]
    assert call(ui + "/api/file?name=../etc/passwd")[0] == 400
    assert call(ui + "/api/file?name=secrets/gitea-token")[0] == 400
    assert call(ui + "/api/file?name=needs/new.yml", "PUT", {"content": "images: [redis:7]\n"})[0] == 200
    assert call(ui + "/api/file?name=needs/new.yml", "PUT", {"content": "images: ["})[0] == 400
    assert json.loads(call(ui + "/api/overview")[1])["needs"]["images"] == 3


def test_run_command_and_poll(ui):
    assert call(ui + "/api/run", "POST", {"args": ["rm", "-rf", "/"]})[0] == 400
    status, body = call(ui + "/api/run", "POST", {"args": ["needs"]})
    run_id = json.loads(body)["id"]
    for _ in range(100):
        state = json.loads(call(ui + f"/api/run/{run_id}")[1])
        if not state["running"]:
            break
        time.sleep(0.1)
    assert state["exit"] == 0 and "image  python:3.12-slim" in state["log"]


def test_overview_has_step_data(ui):
    data = json.loads(call(ui + "/api/overview")[1])
    assert data["needs_files"][0]["name"] == "demo" and data["needs_files"][0]["summary"]["images"] == 2
    assert "gitea" in data["needs_detail"]["files"][0]["name"] or any(
        f["name"] == "gitea" for f in data["needs_detail"]["files"]
    )
    assert data["versions"]["gitea"] and isinstance(data["ssh_aliases"], list) and "running" in data["lab"]
    assert data["servers"]["is_lab"] is False and data["running"] == []


def test_servers_form_writes_valid_yaml(ui, workspace_dir):
    form = {
        "gitea": {"host": "g1", "name": "Gitea.Local"},
        "infrastructures": {
            "dev": {"type": "compose", "nodes": "d1, d2"},
            "k": {"type": "k3s", "nodes": ["k1"], "args": "--disable traefik"},
        },
    }
    assert call(ui + "/api/servers", "POST", form)[0] == 200
    data = json.loads(call(ui + "/api/overview")[1])["servers"]
    assert data["gitea"]["name"] == "gitea.local" and data["infrastructures"]["dev"]["nodes"] == ["d1", "d2"]
    assert data["infrastructures"]["k"]["args"] == "--disable traefik"
    # an invalid form is rejected and the old file is kept
    status, body = call(ui + "/api/servers", "POST", {"gitea": {"host": "", "name": ""}, "infrastructures": {}})
    assert status == 400 and "gitea" in json.loads(body)["error"]
    assert json.loads(call(ui + "/api/overview")[1])["servers"]["gitea"]["host"] == "g1"


def test_project_scan_and_save(ui, workspace_dir, tmp_path):
    project = tmp_path / "my-api"
    project.mkdir()
    (project / "requirements.txt").write_text("flask==3.0.0\nhttpx==0.27.0\n")
    (project / "Dockerfile").write_text(
        "FROM python:3.11-slim AS base\nRUN apt-get update && apt-get install -y --no-install-recommends curl libpq-dev \\\n && rm -rf /var/lib/apt/lists/*\nFROM base\nCOPY . .\n"
    )
    (project / "web").mkdir()
    (project / "web" / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/chalk": {
                        "version": "5.3.0",
                        "resolved": "https://registry.npmjs.org/chalk/-/chalk-5.3.0.tgz",
                        "integrity": "sha512-x",
                    },
                },
            }
        )
    )
    status, body = call(ui + "/api/project/scan", "POST", {"folder": str(project)})
    p = json.loads(body)
    assert status == 200 and p["name"] == "my-api" and p["python"] == "3.11"
    assert p["images"] == ["python:3.11-slim"] and p["apt"] == {"distro": "bookworm", "packages": ["curl", "libpq-dev"]}
    kinds = {Path(lf["path"]).name: (lf["kind"], lf["count"]) for lf in p["lockfiles"]}
    assert kinds == {"requirements.txt": ("python", 2), "package-lock.json": ("js", 1)}
    assert call(ui + "/api/project/scan", "POST", {"folder": str(tmp_path / "nope")})[0] == 400

    body = {
        "name": "My API",
        "python": p["python"],
        "images": p["images"],
        "pypi_files": [str(project / "requirements.txt")],
        "npm_files": [str(project / "web" / "package-lock.json")],
        "pypi": ["rich==13.0.0"],
        "npm": [],
        "apt": p["apt"],
    }
    status, resp = call(ui + "/api/project/save", "POST", body)
    assert status == 200 and json.loads(resp)["saved"] == "needs/my-api.yml"
    text = (workspace_dir / "needs" / "my-api.yml").read_text()
    assert "file: ../../" in text or "file: ../" in text  # relative to needs/
    data = json.loads(call(ui + "/api/overview")[1])
    mine = next(f for f in data["needs_files"] if f["name"] == "my-api")
    assert mine["error"] is None and mine["summary"] == {
        "images": 1,
        "pypi": 3,
        "npm": 1,
        "apt": 2,
        "apk": 0,
        "rpm": 0,
        "files": 0,
    }
    assert "python:3.11-slim" in data["needs_detail"]["images"] and "3.11" in data["needs_detail"]["pypi"]
    assert call(ui + "/api/file?name=needs/my-api.yml", "DELETE")[0] == 200
    assert call(ui + "/api/file?name=servers.yml", "DELETE")[0] == 400
    assert not (workspace_dir / "needs" / "my-api.yml").exists()


def test_lockfile_upload(ui, workspace_dir):
    req = urllib.request.Request(
        ui + "/api/lockfile?project=Shop&filename=requirements.txt", method="PUT", data=b"django==5.0\n"
    )
    with urllib.request.urlopen(req) as resp:
        info = json.loads(resp.read())
    assert info["kind"] == "python" and info["count"] == 1
    assert (workspace_dir / "needs" / "shop" / "requirements.txt").read_text() == "django==5.0\n"
    status, body = call(
        ui + "/api/project/save", "POST", {"name": "shop", "python": "3.12", "pypi_files": [info["path"]]}
    )
    assert status == 200 and "file: shop/requirements.txt" in (workspace_dir / "needs" / "shop.yml").read_text()
    req = urllib.request.Request(ui + "/api/lockfile?project=x&filename=../evil", method="PUT", data=b"")
    with urllib.request.urlopen(req) as resp:
        assert json.loads(resp.read())["error"]  # file name sanitised to "evil", which is not a lockfile
    assert not (workspace_dir / "needs" / "evil").exists()


def test_ssh_check_rejects_odd_aliases(ui):
    assert json.loads(call(ui + "/api/ssh-check", "POST", {"alias": "a b; rm"})[1])["ok"] is False


def test_overview_sync_box_status_and_preflight(ui, workspace_dir):
    data = json.loads(call(ui + "/api/overview")[1])
    assert data["sync"]["arrows"]["fetch"]["state"] == "none" and data["sync"]["arrows"]["setup"]["why"]
    bs = data["box_status"]
    assert bs["missing_total"] >= 4  # images, pypi, npm, apt from the example, plus the pinned files
    assert "python:3.12-slim" in bs["missing"]["images"]
    names = {c["name"] for c in data["preflight"]}
    assert {"ssh", "openssl", "docker", "pip"} <= names
    assert data["runs"] == []


def test_search_and_run_history(ui, workspace_dir):
    status, body = call(ui + "/api/search?q=flask")
    r = json.loads(body)
    assert status == 200 and r["needs"][0]["text"].startswith("flask") and r["box"] == []
    assert json.loads(call(ui + "/api/search?q=f")[1]) == {"needs": [], "box": []}
    # a finished run shows up in history with its exit code
    run_id = json.loads(call(ui + "/api/run", "POST", {"args": ["needs"]})[1])["id"]
    for _ in range(100):
        if not json.loads(call(ui + f"/api/run/{run_id}")[1])["running"]:
            break
        time.sleep(0.1)
    time.sleep(0.3)  # the trailer is written by a thread right after exit
    hist = json.loads(call(ui + "/api/runs")[1])
    assert hist[0]["id"] == run_id and hist[0]["exit"] == 0 and hist[0]["command"] == "outpost needs"
    assert "[outpost exit 0" in json.loads(call(ui + f"/api/runs/{run_id}")[1])["log"]
    assert call(ui + "/api/runs/../../secrets")[0] == 400
    assert json.loads(call(ui + "/api/overview")[1])["runs"][0]["id"] == run_id


def test_server_status_rejects_odd_aliases(ui):
    assert json.loads(call(ui + "/api/server-status", "POST", {"alias": "x;y"})[1])["ok"] is False


def test_ssh_config_problems_are_messages_and_the_environment_still_counts(workspace_dir, monkeypatch, tmp_path):
    from outpost.ui import reach_server, server_status
    from outpost.ui.reach import choose_ssh_config

    workspace = Workspace(workspace_dir)
    monkeypatch.setenv("OUTPOST_SSH_CONFIG", str(tmp_path / "missing"))
    for check in (reach_server, server_status):
        result = check(workspace, "node-1")
        assert result["ok"] is False and "does not exist" in result["message"]
    # servers.yml that does not load: OUTPOST_SSH_CONFIG is still the choice
    config = tmp_path / "ssh_config"
    config.write_text("Host x\n")
    monkeypatch.setenv("OUTPOST_SSH_CONFIG", str(config))
    workspace.servers_file.write_text("gitea: [not, a, mapping]\n")
    assert choose_ssh_config(workspace) == (config, None)


def test_servers_form_round_trips_the_egress_proxy(ui, workspace_dir):
    from outpost.config import load_servers

    form = {
        "gitea": {"host": "g1", "name": "gitea.internal"},
        "infrastructures": {"dev": {"type": "compose", "nodes": "d1"}},
        "egress_proxy": {
            "allow": "api.twilio.com\n*.sendgrid.net, api.twilio.com",
            "host": "",
            "port": "3128",
            "distro": "noble",
        },
    }
    assert call(ui + "/api/servers", "POST", form)[0] == 200
    proxy = load_servers(workspace_dir / "servers.yml").egress_proxy
    assert proxy is not None
    assert (
        proxy.host == "g1"
        and proxy.port == 3128
        and proxy.allow == ["api.twilio.com", "*.sendgrid.net"]
        and proxy.distro == "noble"
    )
    assert "port" not in (workspace_dir / "servers.yml").read_text()  # defaults stay out of the file
    data = json.loads(call(ui + "/api/overview")[1])["servers"]["egress_proxy"]
    assert data["allow"] == ["api.twilio.com", "*.sendgrid.net"] and data["host"] == "g1"
    # a form without the card keeps the section; an empty allow list removes it; a bad entry is refused
    assert call(ui + "/api/servers", "POST", {k: v for k, v in form.items() if k != "egress_proxy"})[0] == 200
    proxy = load_servers(workspace_dir / "servers.yml").egress_proxy
    assert proxy is not None and proxy.allow == ["api.twilio.com", "*.sendgrid.net"]
    status, body = call(
        ui + "/api/servers", "POST", {**form, "egress_proxy": {"allow": "https://api.twilio.com", "port": "3128"}}
    )
    assert status == 400 and "egress_proxy.allow" in json.loads(body)["error"]
    other = {**form, "egress_proxy": {"allow": "api.twilio.com", "host": "p1", "port": "8888"}}
    assert call(ui + "/api/servers", "POST", other)[0] == 200
    proxy = load_servers(workspace_dir / "servers.yml").egress_proxy
    assert proxy is not None and (proxy.host, proxy.port) == ("p1", 8888)
    assert call(ui + "/api/servers", "POST", {**form, "egress_proxy": {"allow": ""}})[0] == 200
    assert load_servers(workspace_dir / "servers.yml").egress_proxy is None


def test_proxy_commands_are_allowed_from_the_page():
    from outpost.ui import ALLOWED_COMMANDS

    assert "proxy" in ALLOWED_COMMANDS


# -- only the dashboard's own page may change anything --------------------------------------


def test_a_foreign_web_page_cannot_run_commands(ui):
    """Bug: POST /api/run took any Origin and a text/plain body, so any web page open in the browser
    could start outpost commands on this laptop (a "simple" cross-site request needs no preflight)."""
    run = {"args": ["needs"]}
    status, body = call(ui + "/api/run", "POST", run, headers={"Content-Type": "text/plain"})
    assert status == 403 and "application/json" in json.loads(body)["error"]
    evil = {"Content-Type": "application/json", "Origin": "https://evil.example"}
    status, body = call(ui + "/api/run", "POST", run, headers=evil)
    assert status == 403 and "evil.example" in json.loads(body)["error"]
    for method, path in (("PUT", "/api/file?name=needs/x.yml"), ("DELETE", "/api/file?name=needs/demo.yml")):
        assert call(ui + path, method, {"content": "a: 1\n"}, headers={"Origin": "null"})[0] == 403
    assert (
        call(ui + "/api/servers", "POST", {"gitea": {}}, headers={"Content-Type": "application/x-www-form-urlencoded"})[
            0
        ]
        == 403
    )
    port = ui.rsplit(":", 1)[1]
    status, body = call(ui + "/api/overview", headers={"Host": f"evil.example:{port}"})  # DNS rebinding
    assert status == 403
    # the page itself: same origin, JSON
    status, body = call(
        ui + "/api/file?name=needs/x.yml",
        "PUT",
        {"content": "a: 1\n"},
        headers={"Origin": ui, "Content-Type": "application/json"},
    )
    assert status == 200
    assert call(ui + "/api/overview", headers={"Host": f"localhost:{port}"})[0] == 200


def test_the_page_sends_json_bodies_as_json():
    page = (Path(__file__).parents[1] / "outpost" / "ui" / "index.html").read_text()
    assert "opts.headers = { 'Content-Type': 'application/json' }" in page


def test_every_get_route_answers(ui, workspace_dir):
    """The routes index.html reads, against a fresh workspace: none of them errors."""
    from outpost.ui.server import DELETE_ROUTES, GET_PREFIX_ROUTES, GET_ROUTES, POST_ROUTES, PUT_ROUTES

    for path in GET_ROUTES:
        status, body = call(ui + path + ("?name=servers.yml" if path == "/api/file" else "?q=flask"))
        assert status == 200, (path, body[:200])
    page = (Path(__file__).parents[1] / "outpost" / "ui" / "index.html").read_text()
    asked = set(re.findall(r"api\(['`](/[a-z/-]+)", page))
    served = set(GET_ROUTES) | set(PUT_ROUTES) | set(POST_ROUTES) | set(DELETE_ROUTES) | set(GET_PREFIX_ROUTES)
    assert len(asked) > 10 and asked <= served, asked - served
    assert call(ui + "/api/runs/20260101-000000-needs")[0] == 200 and call(ui + "/api/run/nope")[0] == 400
    assert set(GET_PREFIX_ROUTES) == {"/api/run/", "/api/runs/"}
    assert call(ui + "/api/nothing")[0] == 404
    status, body = call(ui + "/report.html")
    assert status == 200 and b"What this workspace is" in body


def test_a_broken_mirrors_yml_shows_as_a_needs_error_not_a_blank_page(ui, workspace_dir):
    """Bug: mirrors.yml was read outside the guarded block, so one bad mirrors.yml made all of
    /api/overview a 400 and the dashboard showed nothing."""
    (workspace_dir / "mirrors.yml").write_text("pypi: [not: a url\n")
    status, body = call(ui + "/api/overview")
    data = json.loads(body)
    assert status == 200 and "mirrors.yml" in data["needs"]["error"] and data["needs_detail"] is None
    assert data["servers"]["gitea"]["host"] and data["box_status"]["missing_total"] == 0


def test_needs_detail_is_what_fetch_gets_including_tinyproxy(ui, workspace_dir):
    """The Needs page shows needs.all_needs: tinyproxy is there once the proxy host's distro is known,
    and left out (not an error) while it is not."""
    servers = workspace_dir / "servers.yml"
    servers.write_text(servers.read_text() + "\negress_proxy: {allow: [api.twilio.com]}\n")
    data = json.loads(call(ui + "/api/overview")[1])
    assert data["needs_detail"] and "tinyproxy" not in json.dumps(data["needs_detail"]["apt"])
    servers.write_text(servers.read_text().replace("allow: [api.twilio.com]", "allow: [api.twilio.com], distro: noble"))
    data = json.loads(call(ui + "/api/overview")[1])
    assert "tinyproxy" in data["needs_detail"]["apt"]["noble"]
    assert "tinyproxy (noble)" in data["box_status"]["missing"]["apt"]


def test_backups_are_listed_from_the_workspace(ui, workspace_dir):
    backups = workspace_dir / "backups"
    backups.mkdir()
    (backups / "gitea-20260101-000000.tar.gz").write_bytes(b"x" * 3)
    assert json.loads(call(ui + "/api/backups")[1]) == [
        {
            "file": "backups/gitea-20260101-000000.tar.gz",
            "bytes": 3,
            "when": json.loads(call(ui + "/api/backups")[1])[0]["when"],
        }
    ]
