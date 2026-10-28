"""The workspace folder, the AirGappedSystem that reads it, and the endpoints worked out from it."""

import os

import pytest

from outpost import hosts, ssh, versions
from outpost.config import ConfigError, GiteaConfig
from outpost.endpoints import gitea_endpoint, make_gitea_endpoint, proxy_endpoint, proxy_endpoint_or_none
from outpost.system import AirGappedSystem
from outpost.workspace import (
    GITEA_TOKEN,
    STATE_GITEA,
    STATE_PROXY,
    TEMPLATE_DIR,
    Workspace,
    infra_state,
)
from tests.conftest import FakeHost


@pytest.fixture(autouse=True)
def _reset_globals():
    yield
    ssh.use_config(None)
    versions.use_arch("amd64")


def add_to_servers_yml(workspace_dir, text: str) -> None:
    path = workspace_dir / "servers.yml"
    path.write_text(path.read_text() + text)


# -- Workspace -------------------------------------------------------------------------


def test_workspace_layout_joins_names_to_the_root(tmp_path):
    workspace = Workspace(tmp_path)
    assert workspace.servers_file == tmp_path / "servers.yml" and workspace.box_dir == tmp_path / "box"
    assert workspace.backups_dir == tmp_path / "backups" and workspace.hosts_state_dir == tmp_path / "state" / "hosts"
    assert (
        workspace.runs_dir == tmp_path / "state" / "runs"
        and workspace.report_file == tmp_path / "state" / "report.html"
    )
    assert workspace.ca_cert == tmp_path / "pki" / "ca.crt" and workspace.box_public_key == tmp_path / "pki" / "box.pub"
    assert infra_state("dev") == "infra-dev"


def test_open_needs_a_servers_yml_and_create_copies_the_template(tmp_path):
    with pytest.raises(ConfigError, match="Run `outpost init` here"):
        Workspace.open(tmp_path)
    (tmp_path / "needs").mkdir()
    (tmp_path / "needs" / "demo.yml").write_text("mine")
    workspace = Workspace.create(tmp_path)
    assert Workspace.open(tmp_path).root == workspace.root and (tmp_path / "checks" / "hello-python").is_dir()
    assert (tmp_path / ".gitignore").exists() and (TEMPLATE_DIR / "servers.yml").exists()
    assert (tmp_path / "needs" / "demo.yml").read_text() != "mine"  # folders are merged, files in them replaced


def test_state_and_secrets(tmp_path):
    workspace = Workspace(tmp_path)
    assert workspace.read_state("fetch", {}) == {} and workspace.state_names() == [] and not workspace.state_mtime("x")
    workspace.write_state("fetch", {"b": 1, "a": 2})
    workspace.write_state("upload", {})
    assert workspace.read_state("fetch") == {"a": 2, "b": 1} and workspace.state_names() == ["fetch", "upload"]
    assert workspace.state_mtime("fetch")
    assert workspace.read_secret(GITEA_TOKEN) is None
    path = workspace.write_secret(GITEA_TOKEN, " tok \n")
    assert workspace.read_secret(GITEA_TOKEN) == "tok" and os.stat(path).st_mode & 0o777 == 0o600


# -- AirGappedSystem ----------------------------------------------------------------------


def test_load_reads_servers_and_sets_ssh_and_arch(workspace_dir, monkeypatch):
    add_to_servers_yml(workspace_dir, "arch: arm64\nssh_config: lab/ssh_config\n")
    seen = []
    monkeypatch.setattr(ssh, "use_config", seen.append)
    system = AirGappedSystem.load(workspace_dir)
    assert system.servers.gitea.host == "gitea-1" and system.workspace.root == workspace_dir.resolve()
    assert seen == [(workspace_dir / "lab" / "ssh_config").resolve()] and versions.ARCH == "arm64"


def test_load_explains_a_missing_workspace(tmp_path):
    with pytest.raises(ConfigError, match="No servers.yml"):
        AirGappedSystem.load(tmp_path)


def test_a_broken_mirrors_yml_only_matters_when_mirrors_are_read(workspace_dir):
    (workspace_dir / "mirrors.yml").write_text("nonsense: true\n")
    system = AirGappedSystem.load(workspace_dir)
    with pytest.raises(ConfigError, match="unknown keys"):
        system.mirrors  # noqa: B018  reading the property is the test


def test_hosts_are_made_once_and_come_from_one_name(workspace_dir, monkeypatch):
    import outpost.system as systemmod

    made = []
    monkeypatch.setattr(systemmod, "Host", lambda alias: made.append(alias) or FakeHost(alias))
    add_to_servers_yml(workspace_dir, "egress_proxy: {host: proxy-1, allow: [api.twilio.com]}\n")
    system = AirGappedSystem.load(workspace_dir)
    assert system.host("dev-1") is system.nodes("dev")[0] and system.gitea_host.alias == "gitea-1"
    assert system.proxy_host is not None and system.proxy_host.alias == "proxy-1"
    assert made == ["dev-1", "gitea-1", "proxy-1"]
    with pytest.raises(ConfigError, match="No infrastructure named"):
        system.nodes("nope")


def test_state_secrets_and_the_ca_are_read_on_every_call(workspace_dir):
    system = AirGappedSystem.load(workspace_dir)
    assert system.set_up_infras() == [] and not system.gitea_is_set_up() and system.secret(GITEA_TOKEN) is None
    system.write_state(infra_state("dev"), {"type": "compose"})
    system.write_state(STATE_GITEA, {"ip": "10.0.0.5"})
    system.workspace.write_secret(GITEA_TOKEN, "tok")
    assert [i.name for i in system.set_up_infras()] == ["dev"] and system.gitea_is_set_up()
    assert system.secret(GITEA_TOKEN) == "tok"
    with pytest.raises(RuntimeError, match="pki/ca.crt is missing"):
        system.ca_pem()
    system.workspace.pki_dir.mkdir()
    system.workspace.ca_cert.write_text("CA")
    assert system.ca_pem() == "CA"


# -- endpoints ------------------------------------------------------------------------------


def test_gitea_endpoint_needs_an_address(workspace_dir):
    system = AirGappedSystem.load(workspace_dir)
    with pytest.raises(RuntimeError, match="Run `outpost gitea setup` first"):
        gitea_endpoint(system)
    system.write_state(STATE_GITEA, {"ip": "10.0.0.5"})
    gitea = gitea_endpoint(system)
    assert gitea.ip == "10.0.0.5" and gitea.pypi_index == "https://gitea.internal/api/packages/pkgs/pypi/simple"
    with_ip = GiteaConfig(host="gitea-1", name="gitea.internal", ip="10.0.0.7")
    assert make_gitea_endpoint(with_ip, {"ip": "10.0.0.5"}).ip == "10.0.0.7"  # servers.yml wins


def test_proxy_endpoint_prefers_recorded_then_cached_then_asked(workspace_dir, monkeypatch):
    import outpost.system as systemmod

    monkeypatch.setattr(systemmod, "Host", lambda alias: FakeHost(alias, facts={"ip": "10.0.0.99"}))
    system = AirGappedSystem.load(workspace_dir)
    system.write_state(STATE_GITEA, {"ip": "10.0.0.5"})
    assert proxy_endpoint(system) is None
    add_to_servers_yml(workspace_dir, "egress_proxy: {host: proxy-1, allow: [api.twilio.com]}\n")
    system = AirGappedSystem.load(workspace_dir)
    assert proxy_endpoint(system).ip == "10.0.0.99"  # pyright: ignore[reportOptionalMemberAccess]
    hosts.record(system.workspace, "proxy-1", {"reachable": True, "ip": "10.0.0.50"})
    assert proxy_endpoint(system).ip == "10.0.0.50"  # pyright: ignore[reportOptionalMemberAccess]
    system.write_state(STATE_PROXY, {"host": "proxy-1", "ip": "10.0.0.40"})
    endpoint = proxy_endpoint(system)
    assert endpoint and endpoint.ip == "10.0.0.40" and endpoint.url == "http://proxy.internal:3128"


def test_proxy_endpoint_or_none_notes_an_unknown_address(workspace_dir, monkeypatch):
    import outpost.system as systemmod
    from outpost.ssh import Unreachable

    def unreachable(alias):
        host = FakeHost(alias)
        host.ip = lambda: (_ for _ in ()).throw(Unreachable("[proxy-1] ssh failed"))
        return host

    monkeypatch.setattr(systemmod, "Host", unreachable)
    add_to_servers_yml(workspace_dir, "egress_proxy: {host: proxy-1, allow: [api.twilio.com]}\n")
    system = AirGappedSystem.load(workspace_dir)
    system.write_state(STATE_GITEA, {"ip": "10.0.0.5"})
    notes = []
    assert proxy_endpoint_or_none(system, gitea_endpoint(system), log=notes.append) is None
    assert "the egress proxy's address is unknown" in notes[0]
