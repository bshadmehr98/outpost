"""The Gitea install flow against a fake host: right files, right order, idempotent."""

from contextlib import contextmanager

import pytest

import outpost.gitea
import outpost.system
from outpost import gitea, versions
from outpost.ssh import Result
from outpost.system import AirGappedSystem
from tests.conftest import FakeHost


class FakeClient:
    def ensure_org(self, name):
        return True

    def version(self):
        return "28.2.0"


@pytest.fixture
def prepared(workspace_dir, monkeypatch):
    binary = workspace_dir / "box" / "files" / "gitea" / versions.GITEA / "gitea"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"ELF")
    host = FakeHost(
        "gitea-1",
        facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5"},
        responses=[
            (r"generate-access-token", "abc123token\n"),
            (r"admin user list", ""),
            (r"api/v1/user", "200"),
            (r"test -s /etc/gitea/app.ini", Result(1, "", "")),
        ],
    )
    monkeypatch.setattr(outpost.system, "Host", lambda alias: host)

    @contextmanager
    def fake_connect(system):
        yield FakeClient()

    monkeypatch.setattr(outpost.gitea, "connect", fake_connect)
    return AirGappedSystem.load(workspace_dir), host


def test_setup_installs_everything_in_order(prepared):
    system, host = prepared
    workspace = system.workspace
    info = gitea.setup(system, log=lambda *_: None)
    assert info == {"name": "gitea.internal", "ip": "10.0.0.5", "version": "28.2.0"}

    assert host.puts == [
        (str(workspace.box_dir / "files" / "gitea" / versions.GITEA / "gitea"), "/usr/local/bin/gitea")
    ]
    ini = host.files["/etc/gitea/app.ini"]
    assert "PROTOCOL = https" in ini and "HTTP_PORT = 443" in ini and "ROOT_URL = https://gitea.internal/" in ini
    assert "[packages]\nENABLED = true" in ini and "DISABLE_REGISTRATION = true" in ini
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in host.files["/etc/systemd/system/gitea.service"]
    assert host.files["/etc/gitea/gitea.crt"].startswith("-----BEGIN CERTIFICATE-----")
    assert (workspace.pki_dir / "ca.crt").exists() and (workspace.secrets_dir / "gitea.key").exists()

    joined = "\n".join(host.scripts)
    assert "useradd --system" in joined and "gitea generate secret SECRET_KEY" in joined
    assert "runuser -u git --" in joined and "sudo -u git" not in joined
    assert "admin user create --admin --username outpost" in joined
    assert workspace.read_secret("gitea-token") == "abc123token"
    assert workspace.read_secret("gitea-admin-password")
    assert workspace.read_state("gitea")["ip"] == "10.0.0.5"
    seal = next(i for i, s in enumerate(host.scripts) if s.endswith("outpost-egress seal 'gitea setup'"))
    restart = next(i for i, s in enumerate(host.scripts) if "systemctl restart gitea" in s)
    create_user = next(i for i, s in enumerate(host.scripts) if "admin user create" in s)
    assert restart < create_user < seal
    assert (
        "--scopes write:organization,write:repository,write:package,read:user,read:misc" in joined
        and "--scopes all" not in joined
    )
    assert 'echo "egress=' in host.scripts[-1]  # the host facts are cached last, for the dashboard and the report
    assert (workspace.state_dir / "hosts" / "gitea-1.json").exists()


def test_setup_is_idempotent(prepared):
    system, host = prepared
    workspace = system.workspace
    gitea.setup(system, log=lambda *_: None)
    host.responses = [
        (r"gitea --version", f"Gitea version {versions.GITEA} built with go"),
        (r"admin user list", "outpost\n"),
        (r"test -s /etc/gitea/app.ini", ""),
        (r"api/v1/user", "200"),
    ]
    host.puts.clear()
    host.scripts.clear()
    token_before = workspace.read_secret("gitea-token")
    gitea.setup(system, log=lambda *_: None)
    assert host.puts == []  # binary not re-uploaded
    assert not any("admin user create" in s for s in host.scripts)
    assert not any("generate-access-token" in s for s in host.scripts)
    assert workspace.read_secret("gitea-token") == token_before


def test_setup_needs_the_binary(workspace_dir):
    with pytest.raises(RuntimeError, match="outpost fetch --only files"):
        gitea.setup(AirGappedSystem.load(workspace_dir), log=lambda *_: None)


def test_setup_replaces_a_token_this_gitea_does_not_accept(prepared):
    """After `lab down` and `lab up` (or a reinstall) the old secrets/gitea-token is unknown to Gitea."""
    system, host = prepared
    workspace = system.workspace
    workspace.write_secret("gitea-token", "stale")
    host.responses.insert(0, (r"api/v1/user", "401"))
    gitea.setup(system, log=lambda *a: None)
    assert workspace.read_secret("gitea-token") == "abc123token"


def test_setup_again_keeps_the_proxy_exception_on_the_gitea_host(prepared):
    system, host = prepared
    workspace = system.workspace
    gitea.setup(system, log=lambda *_: None)
    assert "--uid-owner" not in host.files["/usr/local/sbin/outpost-egress"]  # no proxy: the plain seal
    path = workspace.servers_file
    path.write_text(path.read_text() + "\negress_proxy: {allow: [api.twilio.com]}\n")
    workspace.write_state("proxy", {"host": "gitea-1", "url": "http://10.0.0.5:3128", "allow": ["api.twilio.com"]})
    gitea.setup(AirGappedSystem.load(workspace.root), log=lambda *_: None)
    script = host.files["/usr/local/sbin/outpost-egress"]
    assert 'PROXY_USER="outpost-proxy"' in script and "--uid-owner" in script
    # the proxy moved elsewhere: the Gitea host gets the plain seal back
    path.write_text(path.read_text().replace("{allow: [api.twilio.com]}", "{host: proxy-1, allow: [api.twilio.com]}"))
    gitea.setup(AirGappedSystem.load(workspace.root), log=lambda *_: None)
    assert "--uid-owner" not in host.files["/usr/local/sbin/outpost-egress"]


def test_the_token_is_never_on_a_command_line(prepared):
    """`ps` on the Gitea server shows every command line; the token reaches curl on stdin instead."""
    system, host = prepared
    system.workspace.write_secret("gitea-token", "secret-token-123")
    gitea.setup(system, log=lambda *_: None)
    check = next(s for s in host.scripts if "api/v1/user" in s)
    curl_line, *here_document = check.splitlines()
    assert "secret-token-123" not in curl_line and "-H @-" in curl_line
    assert here_document == ["Authorization: token secret-token-123", "OUTPOST_TOKEN_HEADER"]


def test_token_works_hands_curl_the_header_on_stdin(tmp_path):
    """Run the real script with bash and a curl that records what it got."""
    import subprocess

    from outpost.gitea.service import token_works

    (tmp_path / "curl").write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/argv\ncat > {tmp_path}/stdin\nprintf 200\n')
    (tmp_path / "curl").chmod(0o755)

    class LocalHost:
        def run(self, script, raise_on_failure=False, timeout=0):
            done = subprocess.run(
                ["bash", "-c", "set -euo pipefail\n" + script],
                capture_output=True,
                text=True,
                env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            )
            return Result(done.returncode, done.stdout, done.stderr)

    assert token_works(LocalHost(), "secret-token-123")
    assert "secret-token-123" not in (tmp_path / "argv").read_text()
    assert (tmp_path / "stdin").read_text() == "Authorization: token secret-token-123\n"
