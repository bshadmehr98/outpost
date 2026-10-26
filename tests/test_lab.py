from pathlib import Path

import yaml

from outpost import lab, ssh
from outpost.cli import main
from outpost.config import load_servers


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


def test_ssh_use_config_adds_dash_f(monkeypatch):
    monkeypatch.delenv("OUTPOST_SSH_CONFIG", raising=False)
    ssh.use_config("/tmp/some_config")
    try:
        assert ["-F", "/tmp/some_config"] == ssh._base_opts(5)[-2:]
    finally:
        ssh.use_config(None)
    assert "-F" not in ssh._base_opts(5)


def test_lab_command_exists_and_needs_docker(tmp_path, monkeypatch, capsys):
    assert main(["init", str(tmp_path)]) == 0
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert main(["--dir", str(tmp_path), "lab", "up"]) != 0
    assert "Docker" in capsys.readouterr().err
