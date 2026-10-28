import subprocess
from pathlib import Path

import pytest
import yaml

from outpost import lab, ssh
from outpost.cli import main
from outpost.config import load_servers
from outpost.ssh.options import ssh_options


def test_lab_servers_yml_loads_and_points_ssh_at_the_lab(tmp_path):
    (tmp_path / "servers.yml").write_text(lab.servers_yml())
    servers = load_servers(tmp_path / "servers.yml")
    assert servers.gitea.host == "lab-gitea" and servers.gitea.ip == lab.SERVERS["lab-gitea"][0]
    assert set(servers.infrastructures) == {"dev", "prod", "k8s"}
    assert servers.ssh_config == (tmp_path / "lab" / "ssh_config").resolve()
    assert "k8s" not in yaml.safe_load(lab.servers_yml(k3s=False))["infrastructures"]


def test_ssh_config_text_has_every_alias():
    text = lab.ssh_config(Path("/x/id_ed25519"), ["lab-gitea", "lab-node-1"])
    assert "Host lab-gitea\n  HostName 172.31.252.10" in text and "Host lab-node-1" in text
    assert "lab-k3s" not in text and "IdentityFile /x/id_ed25519" in text


def test_ssh_use_config_adds_dash_f(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTPOST_SSH_CONFIG", raising=False)
    config = tmp_path / "ssh_config"
    config.write_text("Host x\n")
    ssh.use_config(config)
    try:
        assert ["-F", str(config)] == ssh_options(5)[-2:]
    finally:
        ssh.use_config(None)
    assert "-F" not in ssh_options(5)


def test_ssh_config_that_does_not_exist_fails_at_once(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTPOST_SSH_CONFIG", raising=False)
    with pytest.raises(ssh.SshError, match="does not exist.*servers.yml"):
        ssh.use_config(tmp_path / "missing")
    monkeypatch.setenv("OUTPOST_SSH_CONFIG", str(tmp_path / "missing"))
    with pytest.raises(ssh.SshError, match="does not exist.*OUTPOST_SSH_CONFIG"):
        ssh.use_config(None)


def test_ssh_config_from_the_environment_or_servers_yml_but_not_two_different(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_text("Host x\n")
    two.write_text("Host y\n")
    monkeypatch.setenv("OUTPOST_SSH_CONFIG", str(one))
    try:
        ssh.use_config(None)
        assert ["-F", str(one)] == ssh_options(5)[-2:]
        ssh.use_config(one)  # the same file twice is fine
        with pytest.raises(ssh.SshError, match="two ssh config files"):
            ssh.use_config(two)
    finally:
        monkeypatch.delenv("OUTPOST_SSH_CONFIG")
        ssh.use_config(None)


def test_lab_command_exists_and_needs_docker(tmp_path, monkeypatch, capsys):
    assert main(["init", str(tmp_path)]) == 0
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert main(["--dir", str(tmp_path), "lab", "up"]) != 0
    assert "Docker" in capsys.readouterr().err


def test_lab_up_forgets_the_old_servers_state_and_cached_facts(tmp_path, monkeypatch):
    """Bug: `lab up` kept state/hosts/*.json, so the dashboard and the report showed the removed
    servers' versions and seal for the brand-new ones."""
    from outpost.workspace import Workspace

    assert main(["init", str(tmp_path)]) == 0
    workspace = Workspace(tmp_path)
    workspace.write_state("gitea", {"version": "old"})
    workspace.write_secret("gitea-token", "old")
    workspace.hosts_state_dir.mkdir(parents=True)
    (workspace.hosts_state_dir / "lab-node-1.json").write_text('{"egress": "sealed"}')
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if cmd[0] == "ssh-keygen":
            key = Path(cmd[cmd.index("-f") + 1])
            key.write_text("private")
            key.with_suffix(".pub").write_text("public")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert lab.up(workspace, k3s=False, log=lambda *_: None) == ["lab-gitea", "lab-node-1", "lab-node-2"]
    assert not list(workspace.hosts_state_dir.glob("*.json")) and workspace.read_state("gitea") is None
    assert workspace.read_secret("gitea-token") is None
    assert load_servers(workspace.servers_file).gitea.host == "lab-gitea"
    assert ["docker", "network", "create", "--subnet", lab.SUBNET, lab.NETWORK] in commands
    assert sum(cmd[:2] == ["docker", "run"] for cmd in commands) == 3
