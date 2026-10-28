"""Backup and restore, the host facts cache, upgrades, error translation, the report, and architectures."""

import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

import outpost.gitea
import outpost.system
from outpost import gitea, hosts, report, versions
from outpost.cli import build_parser, main
from outpost.config import ConfigError, Mirrors, load_servers
from outpost.http import Http
from outpost.infra.upgrade import upgrade_nodes
from outpost.ssh import Result
from outpost.system import AirGappedSystem
from outpost.ui import explain, status
from outpost.workspace import Workspace
from tests.conftest import FakeHost

HOST_FACTS = """id=ubuntu
codename=noble
pretty=Ubuntu 24.04.5 LTS
hostname=node-1
ip=10.0.0.11
arch=x86_64
uptime=3 days, 2 hours
disk_free=23G
disk_pct=61
egress=sealed
egress_boot=enabled
gitea=
docker=29.9.2
compose=5.6.1
buildx=0.38.1
k3s=
coredns=1.15.0
runner=5.2.2
svc_docker=active
svc_outpost-dns=active
containers=2
compose_projects=demo-dev
swarm=active true
stacks=demo-prod
swarm_services=demo-prod_web 2/2,
deployments=
pods=0
"""


@pytest.fixture
def amd64():
    yield
    versions.use_arch("amd64")


# -- backup and restore -----------------------------------------------------------


def test_backup_pauses_gitea_archives_and_copies_home(workspace_dir, monkeypatch):
    host = FakeHost(
        "gitea-1",
        responses=[
            (r"gitea --version", "gitea version 28.2.0 built with go"),
            (r"stat -c %s", "4096"),
            (r"curl -sk", ""),
        ],
    )
    monkeypatch.setattr(outpost.system, "Host", lambda alias: host)
    system = AirGappedSystem.load(workspace_dir)
    workspace = system.workspace
    local = gitea.backup(system, log=lambda *_: None)
    assert local.parent == workspace.backups_dir  # the workspace's backups/, wherever outpost runs
    assert local.exists() and local.name.startswith("gitea-gitea.internal-") and local.suffix == ".gz"
    archive = next(s for s in host.scripts if "tar -C /" in s)
    assert (
        "systemctl stop gitea" in archive
        and "trap 'systemctl start gitea' EXIT" in archive
        and "var/lib/gitea/data etc/gitea" in archive
    )
    assert host.gets[0][0].startswith("/var/tmp/outpost-backups/") and any(
        s.startswith("rm -f /var/tmp/outpost-backups/") for s in host.scripts
    )
    assert workspace.read_state("backup")["gitea"] == "28.2.0" and workspace.read_state("backup")["bytes"] == len(
        b"archive-bytes"
    )
    assert workspace.read_state("backup")["sha256"] == hashlib.sha256(b"archive-bytes").hexdigest()


def test_backup_hashes_the_archive_in_chunks_whatever_its_size(workspace_dir, monkeypatch):
    """The sha256 is read in chunks, so a 3 GB archive is hashed too (it was skipped above 2 GB, and read whole below)."""
    host = FakeHost(
        "gitea-1", responses=[(r"gitea --version", "gitea version 28.2.0 built with go"), (r"stat -c %s", "3000000000")]
    )
    monkeypatch.setattr(outpost.system, "Host", lambda alias: host)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("the archive must not be read whole"))
    system = AirGappedSystem.load(workspace_dir)
    gitea.backup(system, log=lambda *_: None)
    assert system.state("backup")["sha256"] == hashlib.sha256(b"archive-bytes").hexdigest()


def test_backup_needs_an_installed_gitea(workspace_dir, monkeypatch):
    monkeypatch.setattr(outpost.system, "Host", lambda alias: FakeHost(alias))
    with pytest.raises(RuntimeError, match="not installed"):
        gitea.backup(AirGappedSystem.load(workspace_dir), log=lambda *_: None)


def test_restore_moves_current_data_aside_and_checks_the_token(workspace_dir, tmp_path, monkeypatch):
    system = AirGappedSystem.load(workspace_dir)
    workspace = system.workspace
    archive = tmp_path / "gitea-x.tar.gz"
    archive.write_bytes(b"tar")
    workspace.write_secret("gitea-token", "tok")
    host = FakeHost(
        "gitea-1", responses=[(r"gitea --version", "gitea version 28.2.0 built with go"), (r"api/v1/user", "401")]
    )
    monkeypatch.setattr(outpost.system, "Host", lambda alias: host)
    messages = []
    gitea.restore(system, archive, log=messages.append)
    joined = "\n".join(host.scripts)
    assert host.puts == [(str(archive), "/var/tmp/outpost-backups/restore.tar.gz")]
    assert (
        "mv /var/lib/gitea/data /var/lib/gitea/data.before-restore-" in joined
        and "tar -xzf /var/tmp/outpost-backups/restore.tar.gz -C /" in joined
    )
    assert "chown -R git:git /var/lib/gitea" in joined and "systemctl start gitea" in joined
    assert any("does not belong to this data" in m for m in messages)
    assert workspace.read_state("gitea")["restored_from"] == "gitea-x.tar.gz"


# -- upgrades ---------------------------------------------------------------------


def test_gitea_setup_backs_up_before_upgrading(workspace_dir, monkeypatch):
    from contextlib import contextmanager

    binary = workspace_dir / "box" / "files" / "gitea" / versions.GITEA / "gitea"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"ELF")
    host = FakeHost(
        "gitea-1",
        facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5"},
        responses=[
            (r"gitea --version", "gitea version 28.1.0 built with go"),
            (r"stat -c %s", "7"),
            (r"generate-access-token", "tok\n"),
            (r"admin user list", ""),
            (r"api/v1/user", "200"),
            (r"test -s /etc/gitea/app.ini", Result(1, "", "")),
        ],
    )
    monkeypatch.setattr(outpost.system, "Host", lambda alias: host)

    class Client:
        def ensure_org(self, name):
            return False

        def version(self):
            return versions.GITEA

    @contextmanager
    def fake_connect(system):
        yield Client()

    monkeypatch.setattr(outpost.gitea, "connect", fake_connect)
    gitea.setup(AirGappedSystem.load(workspace_dir), log=lambda *_: None)
    tar = next(i for i, s in enumerate(host.scripts) if "tar -C /" in s)
    stop = next(i for i, s in enumerate(host.scripts) if s.startswith("systemctl stop gitea 2>/dev/null"))
    assert (
        tar < stop and host.puts and (workspace_dir / "backups").exists()
    )  # backup first, then the binary is replaced


def test_infra_upgrade_runs_apt_from_gitea_and_reports_failures(workspace_dir):
    system = AirGappedSystem.load(workspace_dir)
    good = FakeHost(
        "dev-1",
        responses=[
            (r"apt-get upgrade", "3 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\nREBOOT-REQUIRED"),
            (r"egress_boot", HOST_FACTS),
        ],
    )
    messages = []
    assert upgrade_nodes(system, [good], log=messages.append)
    assert "apt-get update" in good.scripts[0] and "apt-get upgrade -y" in good.scripts[0]
    assert (
        any("reboot is required" in m for m in messages) and (system.workspace.hosts_state_dir / "dev-1.json").exists()
    )
    bad = FakeHost("dev-1", responses=[(r"apt-get upgrade", "E: Unable to locate package foo")])
    assert not upgrade_nodes(system, [bad], log=messages.append)


def test_infra_upgrade_runs_apk_or_dnf_on_other_families(workspace_dir):
    """One script decides on the node from /etc/os-release, so the same command works on Alpine and Rocky nodes."""
    system = AirGappedSystem.load(workspace_dir)
    quiet = lambda *_: None  # noqa: E731
    alpine_facts = HOST_FACTS.replace("id=ubuntu", "id=alpine\nid_like=\nversion_id=3.20.3")
    host = FakeHost("alp-1", responses=[(r"apk upgrade", "OK: 12 MiB in 20 packages"), (r"egress_boot", alpine_facts)])
    assert upgrade_nodes(system, [host], log=quiet)
    script = host.scripts[0]
    assert (
        script.startswith(". /etc/os-release")
        and "*alpine*)" in script
        and "apk update" in script
        and "apk upgrade" in script
    )
    assert "dnf makecache" in script and "dnf upgrade -y" in script and "needs-restarting -r" in script
    assert "apt-get update -qq" in script and "apt-get upgrade -y" in script  # the Debian branch is still there
    assert json.loads((system.workspace.hosts_state_dir / "alp-1.json").read_text())["id_like"] == ""
    rocky = FakeHost("rocky-1", responses=[(r"dnf upgrade", "Error: Unable to find a match: foo")])
    assert not upgrade_nodes(system, [rocky], log=quiet)


def test_docker_install_restarts_the_daemon_on_a_version_change():
    from outpost.endpoints import GiteaEndpoint
    from outpost.infra import node
    from outpost.infra.context import NodeContext

    ctx = NodeContext(gitea=GiteaEndpoint("gitea.internal", "10.0.0.5", "pkgs"), ca_pem="CA")
    host = FakeHost(responses=[(r"docker --version", "Docker version 29.0.0, build x")])
    node.install_docker(host, ctx, log=lambda *_: None)
    assert any("systemctl restart docker" in s for s in host.scripts) and not any(
        "enable --now docker" in s for s in host.scripts
    )


# -- host facts -------------------------------------------------------------------


def test_inspect_parses_facts_services_and_workloads():
    data = hosts.inspect(FakeHost(responses=[(r"egress_boot", HOST_FACTS)]))
    assert data["reachable"] and data["pretty"] == "Ubuntu 24.04.5 LTS" and data["egress"] == "sealed"
    assert data["installed"]["docker"] == "29.9.2" and data["installed"]["gitea"] == ""
    assert data["services"] == {"docker": "active", "outpost-dns": "active"}
    assert data["workloads"]["compose_projects"] == ["demo-dev"] and data["workloads"]["stacks"] == ["demo-prod"]
    assert data["workloads"]["services"] == ["demo-prod_web 2/2"] and data["workloads"]["containers"] == 2
    assert hosts.drift(data) == []
    data["installed"]["docker"] = "29.0.0"
    assert hosts.drift(data) == [f"docker 29.0.0 (pinned {versions.DOCKER})"]


def test_inspect_reports_an_unreachable_host_and_refresh_caches(workspace_dir):
    assert hosts.inspect(FakeHost()) == {"reachable": False, "error": "no answer"}
    workspace, servers = Workspace(workspace_dir), load_servers(workspace_dir / "servers.yml")
    out = hosts.refresh(
        workspace,
        servers,
        log=lambda *_: None,
        make_host=lambda a: FakeHost(a, responses=[(r"egress_boot", HOST_FACTS)]),
    )
    assert set(out) == {"gitea-1", "dev-1"} and hosts.cached(workspace)["dev-1"]["ip"] == "10.0.0.11"
    assert hosts.roles(servers) == {"gitea-1": ["gitea"], "dev-1": ["dev: compose"]}


# -- translations and summaries ---------------------------------------------------


def test_explain_knows_the_common_failures():
    assert explain.explain("x\nssh: connect to host dev-1 port 22: Connection timed out\nerror: boom")[
        "title"
    ].startswith("The server did not answer")
    assert "passwordless sudo" in explain.explain("sudo: a password is required")["fix"]
    assert (
        explain.explain("error: [dev-1] cannot reach https://gitea.internal: curl: (7)")["title"]
        == "A node cannot reach Gitea."
    )
    assert explain.explain("pip: No matching distribution found for flassk==1")["title"].startswith("A Python package")
    assert explain.explain("everything fine") is None
    assert explain.explain("error: something odd")["title"] == "The command stopped with an error."


def test_summaries_read_like_history():
    assert (
        explain.summarize(
            ["upload"], "    files: 3 uploaded, 10 already there\n    pypi: 0 uploaded, 5 already there\n", 0
        )
        == "Uploaded 3 new files to Gitea, 15 were already there"
    )
    assert (
        explain.summarize(["fetch"], "Box: 62 files, 666 MB in /x\n", 0)
        == "Fetched everything; the box has 62 files, 666 MB"
    )
    assert (
        explain.summarize(["infra", "setup", "dev"], "  dev-1: all good\n", 0)
        == "Set up and verified dev: 1 node all good"
    )
    assert explain.summarize(["check", "--infra", "dev"], "1/1 checks passed\n", 0) == "Smoke checks on dev: 1/1 passed"
    assert explain.summarize(["egress", "dev", "open"], "", 0) == "Switched the internet ON for dev"
    assert explain.summarize(["fetch"], "error: No space left on device", 1) == "failed: A disk is full."
    assert explain.summarize(["fetch"], "", None) == "was cut short"


def test_steps_and_box_changes(tmp_path):
    log = "$ outpost gitea setup\n==> [1/3] connecting\nhello\n==> [2/3] certificates\n"
    assert status.steps(log) == [
        {"text": "connecting", "n": 1, "of": 3, "done": True},
        {"text": "certificates", "n": 2, "of": 3, "done": False},
    ]
    assert status.steps(log + "[outpost exit 0 after 3s]\n")[-1]["done"] is True
    from outpost.needs import Needs, NpmNeed

    p = Workspace(tmp_path)
    (p.box_dir / "npm").mkdir(parents=True)
    (p.box_dir / "npm" / "chalk-5.3.0.tgz").write_bytes(b"x")
    (p.box_dir / "npm" / "old-1.0.0.tgz").write_bytes(b"xx")
    (p.box_dir / "images" / "redis_7").mkdir(parents=True)
    c = status.box_changes(p, Needs(npm=[NpmNeed("chalk", "5.3.0")]))
    assert c["new_files"] == 2 and c["unneeded"] == ["image redis_7", "npm old-1.0.0.tgz"]
    p.write_state("upload", {})
    assert status.box_changes(p, Needs(npm=[NpmNeed("chalk", "5.3.0")]))["new_files"] == 0


# -- the report -------------------------------------------------------------------


def test_report_says_what_is_proven(workspace_dir):
    workspace, servers = Workspace(workspace_dir), load_servers(workspace_dir / "servers.yml")
    data = report.build(workspace, servers)
    assert report.verdict(data)["ok"] is False and "Not fully proven" in report.render(data)
    workspace.write_state("gitea", {"name": "gitea.internal", "ip": "10.0.0.5", "host": "gitea-1", "version": "28.2.0"})
    workspace.write_state(
        "verify-dev", {"result": "ok", "nodes": {"dev-1": [{"name": "egress sealed", "ok": True, "detail": ""}]}}
    )
    hosts.record(workspace, "gitea-1", {**hosts.inspect(FakeHost(responses=[(r"egress_boot", HOST_FACTS)]))})
    hosts.record(workspace, "dev-1", {**hosts.inspect(FakeHost(responses=[(r"egress_boot", HOST_FACTS)]))})
    data = report.build(workspace, servers)
    v = report.verdict(data)
    assert v["ok"] and v["sealed"] == ["dev-1", "gitea-1"] and v["verified"] == ["dev"]
    html = report.render(data)
    assert "Sealed and verified" in html and "egress sealed" in html and "switched off" in html
    out = report.write(workspace, servers)
    assert out == workspace.state_dir / "report.html" and out.read_text().startswith("<!doctype html>")
    assert main(["--dir", str(workspace_dir), "report"]) == 0


# -- architectures ----------------------------------------------------------------


def test_arm64_changes_every_download_name(amd64):
    versions.use_arch("aarch64")
    urls = {f["name"]: f["url"] for f in versions.pinned_files(Mirrors(), {"compose", "k3s"})}
    assert (
        urls["gitea"].endswith("linux-arm64")
        and urls["coredns"].endswith("linux_arm64.tgz")
        and urls["gitea-runner"].endswith("linux-arm64")
    )
    assert urls["docker"] == f"https://download.docker.com/linux/static/stable/aarch64/docker-{versions.DOCKER}.tgz"
    assert urls["docker-compose"].endswith("docker-compose-linux-aarch64") and urls["docker-buildx"].endswith(
        "linux-arm64"
    )
    assert urls["k3s"].endswith("/k3s-arm64") and urls["k3s-airgap-images"].endswith("k3s-airgap-images-arm64.tar.gz")
    assert "manylinux2014_aarch64" in versions.arch_names()["pip"]
    versions.use_arch("amd64")
    assert versions.pinned_files(Mirrors(), set())[0]["url"].endswith("linux-amd64")
    with pytest.raises(ValueError):
        versions.use_arch("mips")


def test_servers_yml_arch_key(workspace_dir, amd64):
    f = workspace_dir / "servers.yml"
    f.write_text(f.read_text() + "\narch: aarch64\n")
    assert load_servers(f).arch == "arm64"
    f.write_text(f.read_text().replace("arch: aarch64", "arch: sparc"))
    with pytest.raises(ConfigError, match="unknown arch"):
        load_servers(f)


def test_setup_refuses_a_node_of_the_wrong_architecture():
    from outpost.endpoints import GiteaEndpoint
    from outpost.infra import compose
    from outpost.infra.context import NodeContext

    ctx = NodeContext(gitea=GiteaEndpoint("gitea.internal", "10.0.0.5", "pkgs"), ca_pem="CA")
    with pytest.raises(RuntimeError, match="aarch64 but this platform is built for x86_64"):
        compose.setup(ctx, [FakeHost(facts={"ip": "10.0.0.11", "arch": "aarch64"})], log=lambda *_: None)


# -- retries ----------------------------------------------------------------------


def test_http_retries_reads_on_network_errors(monkeypatch):
    http = Http("http://127.0.0.1:9")
    calls = []

    class Resp:
        status, headers = 200, {}

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def open_(req, timeout):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return Resp()

    monkeypatch.setattr(http, "_opener", type("O", (), {"open": staticmethod(open_)})())
    monkeypatch.setattr(Http, "_sleep", staticmethod(lambda s: None))
    assert http.get("/x", ok=(200,)).body == b"ok" and len(calls) == 3
    calls.clear()
    with pytest.raises(urllib.error.URLError):
        http.request("POST", "/x", data=b"1")
    assert len(calls) == 1  # writes are not retried on network errors


# -- the command line -------------------------------------------------------------


def test_new_commands_parse_and_explain_themselves(workspace_dir, capsys):
    p = build_parser()
    assert p.parse_args(["gitea", "backup", "--out", "b"]).out == "b"
    assert p.parse_args(["gitea", "restore", "backups/x.tar.gz"]).archive == "backups/x.tar.gz"
    assert p.parse_args(["infra", "upgrade", "dev"]).action == "upgrade"
    assert p.parse_args(["pki", "rotate"]).action == "rotate" and p.parse_args(["hosts", "--refresh"]).refresh
    assert main(["--dir", str(workspace_dir), "gitea", "restore"]) == 1
    assert "needs the archive" in capsys.readouterr().err
    assert main(["--dir", str(workspace_dir), "pki", "rotate"]) == 1
    assert "nothing to rotate" in capsys.readouterr().err
