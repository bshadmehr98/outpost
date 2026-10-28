import json

import yaml

from outpost import demo
from outpost.cli import main
from outpost.config import load_servers
from outpost.endpoints import GiteaEndpoint
from outpost.infra import runner
from outpost.infra.context import NodeContext
from outpost.workspace import Workspace
from tests.conftest import FakeHost

SERVERS = """
gitea: {host: g, name: gitea.internal, ip: 10.0.0.5}
infrastructures:
  dev:  {type: compose, nodes: [n1]}
  prod: {type: swarm, nodes: [n1, n2]}
  k8s:  {type: k3s, nodes: [k1]}
"""


def test_workflow_is_valid_yaml_with_one_deploy_job_per_infrastructure():
    text = demo.workflow("gitea.internal", "pkgs", {"dev": "compose", "prod": "swarm", "k8s": "k3s"}, "outpost")
    wf = yaml.safe_load(text)
    assert list(wf["jobs"]) == ["build", "dev", "prod", "k8s"]
    assert (
        wf["jobs"]["build"]["runs-on"] == "docker"
        and wf["jobs"]["k8s"]["runs-on"] == "k8s"
        and wf["jobs"]["k8s"]["needs"] == "build"
    )
    assert wf["env"]["IMAGE"] == "gitea.internal/pkgs/demo"
    steps = [s["run"] for s in wf["jobs"]["dev"]["steps"]]
    assert "docker compose -p demo-dev" in steps[1] and "PORT=8000" in steps[1] and "${{ gitea.sha }}" in steps[1]
    assert "127.0.0.1:8000/healthz" in steps[2]
    assert (
        "docker stack deploy" in wf["jobs"]["prod"]["steps"][1]["run"]
        and "PORT=8080" in wf["jobs"]["prod"]["steps"][1]["run"]
    )
    assert (
        "__PORT__|30080" in wf["jobs"]["k8s"]["steps"][1]["run"]
        and "k3s kubectl apply" in wf["jobs"]["k8s"]["steps"][1]["run"]
    )
    assert (
        "secrets.PACKAGE_TOKEN" in wf["jobs"]["build"]["steps"][2]["run"]
        and "-u outpost" in wf["jobs"]["build"]["steps"][2]["run"]
    )
    assert not any("uses" in s for job in wf["jobs"].values() for s in job["steps"])


def test_init_ships_the_demo_and_ensure_local_fills_an_old_platform(tmp_path):
    assert main(["init", str(tmp_path)]) == 0
    assert (tmp_path / "demo" / "Dockerfile").exists() and (tmp_path / "needs" / "demo.yml").exists()
    files = demo.project_files(tmp_path / "demo")
    assert {
        "Dockerfile",
        "requirements.txt",
        "app/app.py",
        "app/probe.py",
        "app/templates/index.html",
        "compose.yml",
        "stack.yml",
        "k8s.yml",
    } <= set(files)
    dockerfile = files["Dockerfile"].decode()
    assert "COPY --from=config / /" in dockerfile and "--fail-if-reachable" in dockerfile
    assert (
        "FROM ${GITEA}/${ORG}/library/python:3.12-slim" in dockerfile and "\nFROM python:" not in dockerfile
    )  # named by its Gitea copy
    old = tmp_path / "old"
    old.mkdir()
    (old / "servers.yml").write_text(SERVERS)
    (old / "needs").mkdir()
    demo.ensure_local(Workspace(old))
    assert (old / "demo" / "app" / "app.py").exists() and "../demo/requirements.txt" in (
        old / "needs" / "demo.yml"
    ).read_text()


def test_urls_come_from_setup_state(tmp_path):
    (tmp_path / "servers.yml").write_text(SERVERS)
    workspace = Workspace(tmp_path)
    workspace.write_state("infra-dev", {"type": "compose", "nodes": {"n1": {"ip": "10.0.0.11"}}})
    u = demo.urls(workspace, load_servers(workspace.servers_file))
    assert u["dev"] == {
        "type": "compose",
        "node": "n1",
        "ip": "10.0.0.11",
        "port": 8000,
        "url": "http://10.0.0.11:8000",
        "set_up": True,
    }
    assert u["k8s"]["set_up"] is False and u["k8s"]["url"] is None and u["k8s"]["port"] == 30080


class FakeClient:
    def __init__(self):
        self.repos, self.secrets, self.commits, self.files = set(), {}, [], {}
        self.runner_names = ["n1"]

    def ensure_repo(self, owner, name, description=""):
        new = (owner, name) not in self.repos
        self.repos.add((owner, name))
        return new

    def set_secret(self, owner, repo, name, value):
        self.secrets[name] = value

    def repo_files(self, owner, repo):
        return {p: demo._blob_sha(c) for p, c in self.files.items()}

    def commit_files(self, owner, repo, files, message, existing):
        self.commits.append(files)
        self.files.update(files)
        return {"commit": {"sha": "abc123def456"}}

    def runners(self):
        return [{"name": n} for n in self.runner_names]

    def create_token(self, user, password, name, scopes):
        assert password == "pw" and "write:package" in scopes
        self.tokens_made = getattr(self, "tokens_made", 0) + 1
        return "pkgtoken"

    def tokens(self, user, password):
        assert password == "pw"
        return (
            [{"name": "outpost-packages", "token_last_eight": "pkgtoken"[-8:]}]
            if getattr(self, "tokens_made", 0)
            else []
        )


def test_push_commits_only_changes_and_makes_one_package_token(tmp_path, monkeypatch, capsys):
    assert main(["init", str(tmp_path)]) == 0
    (tmp_path / "servers.yml").write_text(SERVERS)
    workspace = Workspace(tmp_path)
    workspace.write_state("gitea", {"ip": "10.0.0.5"})
    workspace.write_state("infra-dev", {"type": "compose", "nodes": {"n1": {"ip": "10.0.0.11"}}})
    workspace.write_secret("gitea-admin-password", "pw")
    client = FakeClient()

    class Conn:
        def __enter__(self):
            return client

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(demo, "connect", lambda p, s: Conn())
    servers = load_servers(workspace.servers_file)

    result = demo.push(workspace, servers)
    assert (
        client.secrets == {"PACKAGE_TOKEN": "pkgtoken"} and workspace.read_secret("gitea-package-token") == "pkgtoken"
    )
    assert len(client.commits) == 1 and ".gitea/workflows/deploy.yml" in client.commits[0]
    wf = yaml.safe_load(client.commits[0][".gitea/workflows/deploy.yml"])
    assert list(wf["jobs"]) == ["build", "dev"]  # only set-up infrastructures get a job
    assert (
        result["infras"]["dev"]["url"] == "http://10.0.0.11:8000"
        and (tmp_path / "demo" / ".gitea" / "workflows" / "deploy.yml").exists()
    )
    assert json.loads((tmp_path / "state" / "demo.json").read_text())["commit"] == "abc123def456"

    demo.push(workspace, servers)
    assert len(client.commits) == 1 and client.tokens_made == 1  # nothing changed, token kept
    (tmp_path / "demo" / "README.md").write_text("changed\n")
    demo.push(workspace, servers)
    assert list(client.commits[1]) == ["README.md"]
    assert "nothing changed" in capsys.readouterr().out


def test_push_refuses_without_a_set_up_infrastructure(tmp_path, monkeypatch):
    assert main(["init", str(tmp_path)]) == 0
    (tmp_path / "servers.yml").write_text(SERVERS)
    Workspace(tmp_path).write_state("gitea", {"ip": "10.0.0.5"})
    import pytest

    with pytest.raises(RuntimeError, match="infra setup"):
        demo.push(Workspace(tmp_path), load_servers(tmp_path / "servers.yml"))


def test_runner_labels_and_install(tmp_path):
    (tmp_path / "servers.yml").write_text(SERVERS)
    servers = load_servers(tmp_path / "servers.yml")
    assert runner.labels_for(servers, "n1") == ["dev:host", "prod:host", "docker:host"]
    assert runner.labels_for(servers, "k1") == ["k8s:host"]
    assert runner.labels_for(servers, "n2") == []

    ctx = NodeContext(
        gitea=GiteaEndpoint(name="gitea.internal", ip="10.0.0.5", org="pkgs"), ca_pem="CA", infra_name="dev"
    )
    host = FakeHost("n1", responses=[(r"gitea-runner --version", "")])
    out = runner.install(host, ctx, ["dev:host", "docker:host"], "REGTOKEN", registered=set())
    assert out == {"node": "n1", "labels": ["dev:host", "docker:host"]}
    assert host.ran("generic/gitea-runner/5.2.2/gitea-runner") and host.ran(
        "register --config /etc/outpost/runner.yaml --no-interactive --instance https://gitea.internal --token REGTOKEN --name n1 --labels dev:host,docker:host"
    )
    unit = host.files["/etc/systemd/system/outpost-runner.service"]
    assert (
        "daemon --config /etc/outpost/runner.yaml --labels dev:host,docker:host" in unit
        and "WorkingDirectory=/var/lib/outpost-runner" in unit
    )
    assert "workdir_parent: /var/lib/outpost-runner/work" in host.files["/etc/outpost/runner.yaml"]

    # already registered and known to Gitea: no second registration, labels still refreshed
    host2 = FakeHost(
        "n1", responses=[(r"gitea-runner --version", "5.2.2"), (r"test -s /var/lib/outpost-runner/.runner", "")]
    )
    runner.install(host2, ctx, ["dev:host"], "T", registered={"n1"})
    assert not host2.ran("register --config") and not host2.ran("curl --cacert")
    assert runner.extra_checks()[0][0] == "actions runner active"


def test_push_replaces_a_package_token_gitea_does_not_know(tmp_path, monkeypatch):
    assert main(["init", str(tmp_path)]) == 0
    (tmp_path / "servers.yml").write_text(SERVERS)
    workspace = Workspace(tmp_path)
    workspace.write_state("gitea", {"ip": "10.0.0.5"})
    workspace.write_state("infra-dev", {"type": "compose", "nodes": {"n1": {"ip": "10.0.0.11"}}})
    workspace.write_secret("gitea-admin-password", "pw")
    workspace.write_secret("gitea-package-token", "from-a-previous-gitea")
    client = FakeClient()

    class Conn:
        def __enter__(self):
            return client

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(demo, "connect", lambda p, s: Conn())
    demo.push(workspace, load_servers(workspace.servers_file))
    assert (
        client.tokens_made == 1
        and workspace.read_secret("gitea-package-token") == "pkgtoken"
        and client.secrets["PACKAGE_TOKEN"] == "pkgtoken"
    )
