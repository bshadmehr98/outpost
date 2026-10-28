"""The Gitea install flow against a fake host: right files, right order, idempotent."""

from contextlib import contextmanager

import pytest

from outpost import versions
from outpost.config import load_servers
from outpost.gitea import setup as setupmod
from outpost.paths import Platform
from outpost.ssh import Result
from tests.conftest import FakeHost


class FakeClient:
    def ensure_org(self, name):
        return True

    def version(self):
        return "28.2.0"


@pytest.fixture
def prepared(platform_dir, monkeypatch):
    binary = platform_dir / "box" / "files" / "gitea" / versions.GITEA / "gitea"
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
    monkeypatch.setattr(setupmod, "Host", lambda alias: host)

    @contextmanager
    def fake_connect(platform, servers):
        yield FakeClient()

    monkeypatch.setattr(setupmod, "connect", fake_connect)
    return Platform(platform_dir), load_servers(platform_dir / "servers.yml"), host


def test_setup_installs_everything_in_order(prepared):
    platform, servers, host = prepared
    info = setupmod.setup(platform, servers, log=lambda *_: None)
    assert info == {"name": "gitea.internal", "ip": "10.0.0.5", "version": "28.2.0"}

    assert host.puts == [(str(platform.box_dir / "files" / "gitea" / versions.GITEA / "gitea"), "/usr/local/bin/gitea")]
    ini = host.files["/etc/gitea/app.ini"]
    assert "PROTOCOL = https" in ini and "HTTP_PORT = 443" in ini and "ROOT_URL = https://gitea.internal/" in ini
    assert "[packages]\nENABLED = true" in ini and "DISABLE_REGISTRATION = true" in ini
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in host.files["/etc/systemd/system/gitea.service"]
    assert host.files["/etc/gitea/gitea.crt"].startswith("-----BEGIN CERTIFICATE-----")
    assert (platform.pki_dir / "ca.crt").exists() and (platform.secrets_dir / "gitea.key").exists()

    joined = "\n".join(host.scripts)
    assert "useradd --system" in joined and "gitea generate secret SECRET_KEY" in joined
    assert "runuser -u git --" in joined and "sudo -u git" not in joined
    assert "admin user create --admin --username outpost" in joined
    assert platform.read_secret("gitea-token") == "abc123token"
    assert platform.read_secret("gitea-admin-password")
    assert platform.read_state("gitea")["ip"] == "10.0.0.5"
    seal = next(i for i, s in enumerate(host.scripts) if s.endswith("outpost-egress seal gitea setup"))
    restart = next(i for i, s in enumerate(host.scripts) if "systemctl restart gitea" in s)
    create_user = next(i for i, s in enumerate(host.scripts) if "admin user create" in s)
    assert restart < create_user < seal
    assert (
        "--scopes write:organization,write:repository,write:package,read:user,read:misc" in joined
        and "--scopes all" not in joined
    )
    assert 'echo "egress=' in host.scripts[-1]  # the host facts are cached last, for the dashboard and the report
    assert (platform.state_dir / "hosts" / "gitea-1.json").exists()


def test_setup_is_idempotent(prepared):
    platform, servers, host = prepared
    setupmod.setup(platform, servers, log=lambda *_: None)
    host.responses = [
        (r"gitea --version", f"Gitea version {versions.GITEA} built with go"),
        (r"admin user list", "outpost\n"),
        (r"test -s /etc/gitea/app.ini", ""),
        (r"api/v1/user", "200"),
    ]
    host.puts.clear()
    host.scripts.clear()
    token_before = platform.read_secret("gitea-token")
    setupmod.setup(platform, servers, log=lambda *_: None)
    assert host.puts == []  # binary not re-uploaded
    assert not any("admin user create" in s for s in host.scripts)
    assert not any("generate-access-token" in s for s in host.scripts)
    assert platform.read_secret("gitea-token") == token_before


def test_setup_needs_the_binary(platform_dir):
    with pytest.raises(RuntimeError, match="outpost fetch --only files"):
        setupmod.setup(Platform(platform_dir), load_servers(platform_dir / "servers.yml"), log=lambda *_: None)


def test_setup_replaces_a_token_this_gitea_does_not_accept(prepared):
    """After `lab down` and `lab up` (or a reinstall) the old secrets/gitea-token is unknown to Gitea."""
    platform, servers, host = prepared
    platform.write_secret("gitea-token", "stale")
    host.responses.insert(0, (r"api/v1/user", "401"))
    setupmod.setup(platform, servers, log=lambda *a: None)
    assert platform.read_secret("gitea-token") == "abc123token"


def test_setup_again_keeps_the_proxy_exception_on_the_gitea_host(prepared):
    platform, servers, host = prepared
    setupmod.setup(platform, servers, log=lambda *_: None)
    assert "--uid-owner" not in host.files["/usr/local/sbin/outpost-egress"]  # no proxy: the plain seal
    path = platform.servers_file
    path.write_text(path.read_text() + "\negress_proxy: {allow: [api.twilio.com]}\n")
    platform.write_state("proxy", {"host": "gitea-1", "url": "http://10.0.0.5:3128", "allow": ["api.twilio.com"]})
    setupmod.setup(platform, load_servers(path), log=lambda *_: None)
    script = host.files["/usr/local/sbin/outpost-egress"]
    assert 'PROXY_USER="outpost-proxy"' in script and "--uid-owner" in script
    # the proxy moved elsewhere: the Gitea host gets the plain seal back
    path.write_text(path.read_text().replace("{allow: [api.twilio.com]}", "{host: proxy-1, allow: [api.twilio.com]}"))
    setupmod.setup(platform, load_servers(path), log=lambda *_: None)
    assert "--uid-owner" not in host.files["/usr/local/sbin/outpost-egress"]
