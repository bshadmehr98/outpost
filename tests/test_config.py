import pytest

from outpost.config import ConfigError, load_mirrors, load_servers


def test_servers_load(tmp_path):
    (tmp_path / "servers.yml").write_text(
        "gitea: {host: g1, name: Gitea.Internal, ip: 10.0.0.5}\n"
        "infrastructures:\n  dev: {type: compose, nodes: [d1]}\n  prod: {type: swarm, nodes: [p1, p2], allow: [203.0.113.0/24]}\n"
        "  lab: {type: k3s, nodes: [k1], args: '--disable traefik'}\n"
    )
    servers = load_servers(tmp_path / "servers.yml")
    assert servers.gitea.name == "gitea.internal"
    assert servers.gitea.url == "https://gitea.internal"
    assert servers.gitea.org == "pkgs"
    assert servers.infra_types == {"compose", "swarm", "k3s"}
    assert servers.infra("lab").args == "--disable traefik" and servers.infra("dev").args == ""
    assert servers.infra("prod").allow == ["203.0.113.0/24"]
    with pytest.raises(ConfigError, match="No infrastructure named 'nope'"):
        servers.infra("nope")


@pytest.mark.parametrize(
    "text,message",
    [
        ("infrastructures: {}", "needs a `gitea:` section"),
        ("gitea: {host: g1, name: g}\ninfrastructures:\n  x: {type: nomad, nodes: [a]}", "type must be one of"),
        ("gitea: {host: g1, name: g}\ninfrastructures:\n  x: {type: k3s, nodes: []}", "at least one SSH alias"),
        ("- not a mapping", "must be a mapping"),
        ("gitea: [", "not valid YAML"),
    ],
)
def test_servers_errors(tmp_path, text, message):
    (tmp_path / "servers.yml").write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_servers(tmp_path / "servers.yml")


def test_servers_missing(tmp_path):
    with pytest.raises(ConfigError, match="outpost init"):
        load_servers(tmp_path / "servers.yml")


def test_mirrors_defaults_and_override(tmp_path):
    assert load_mirrors(tmp_path / "mirrors.yml").pypi == "https://pypi.org/simple"
    (tmp_path / "mirrors.yml").write_text("docker: https://docker.example.ir/\n")
    m = load_mirrors(tmp_path / "mirrors.yml")
    assert m.docker == "https://docker.example.ir"
    assert m.npm == "https://registry.npmjs.org"
    (tmp_path / "mirrors.yml").write_text("dockerhub: x\n")
    with pytest.raises(ConfigError, match="unknown keys: dockerhub"):
        load_mirrors(tmp_path / "mirrors.yml")
