import pytest

from outpost import versions
from outpost.config import ConfigError, Mirrors, ProxyConfig
from outpost.needs import (
    FileNeed,
    Needs,
    NpmNeed,
    add_pinned_files,
    all_needs,
    load_needs,
    proxy_package_need,
    read_needs_file,
)
from outpost.system import AirGappedSystem


def test_merge_two_files(tmp_path):
    needs_dir = tmp_path / "needs"
    needs_dir.mkdir()
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")
    (needs_dir / "a.yml").write_text(
        "python: '3.12'\nimages: [python:3.12-slim, postgres:16]\npypi:\n  - file: ../requirements.txt\n  - httpx==0.27.0\n"
        "npm: [chalk@5.3.0, '@scope/pkg@1.0.0']\napt: {distro: bookworm, packages: [curl, gcc]}\n"
        "files:\n  - {url: https://example.com/dl/tool-1.0.tgz, name: tool, version: '1.0'}\n"
    )
    (needs_dir / "b.yml").write_text(
        "python: '3.11'\nimages: [postgres:16, redis:7]\npypi: [django==5.0]\napt:\n  - {distro: bookworm, packages: [curl, libpq-dev]}\n  - {distro: noble, packages: [htop]}\n"
    )
    needs = load_needs(needs_dir)
    assert needs.images == ["python:3.12-slim", "postgres:16", "redis:7"]
    assert needs.pypi == {"3.12": ["flask==3.0.0", "httpx==0.27.0"], "3.11": ["django==5.0"]}
    assert needs.npm == [NpmNeed("chalk", "5.3.0"), NpmNeed("@scope/pkg", "1.0.0")]
    assert needs.apt == {"bookworm": ["curl", "gcc", "libpq-dev"], "noble": ["htop"]}
    assert needs.files == [FileNeed("tool", "1.0", "https://example.com/dl/tool-1.0.tgz", "tool-1.0.tgz")]
    assert needs.sources == ["a.yml", "b.yml"]
    assert needs.summary() == {"images": 3, "pypi": 3, "npm": 2, "apt": 4, "apk": 0, "rpm": 0, "files": 1}


def test_apk_and_rpm_blocks_merge_like_apt(tmp_path):
    needs_dir = tmp_path / "needs"
    needs_dir.mkdir()
    (needs_dir / "a.yml").write_text(
        "apk: {branch: v3.20, packages: [curl, jq]}\n"
        "rpm:\n  - {distro: rocky9, packages: [curl]}\n  - {distro: fedora40, packages: [git]}\n"
    )
    (needs_dir / "b.yml").write_text(
        "apk:\n  - {branch: v3.20, packages: [curl, git]}\n  - {branch: v3.19, packages: [curl]}\n"
        "rpm: {distro: rocky9, packages: [curl, jq]}\n"
    )
    needs = load_needs(needs_dir)
    assert needs.apk == {"v3.20": ["curl", "jq", "git"], "v3.19": ["curl"]}
    assert needs.rpm == {"rocky9": ["curl", "jq"], "fedora40": ["git"]}
    assert needs.apt == {} and not needs.is_empty()
    assert needs.summary() == {"images": 0, "pypi": 0, "npm": 0, "apt": 0, "apk": 4, "rpm": 3, "files": 0}


def test_empty_and_missing(tmp_path):
    assert load_needs(tmp_path / "nope").is_empty()


@pytest.mark.parametrize(
    "text,message",
    [
        ("pypi: [{foo: bar}]", "pip specs"),
        ("npm: [chalk]", "name@version"),
        ("apt: {packages: [curl]}", "distro"),
        ("apk: {packages: [curl]}", "apk needs `branch:`"),
        ("rpm: [{packages: [curl]}]", "rpm needs `distro:`"),
        ("files: [{url: https://x/y}]", "`name:`"),
        ("pypi: [{file: missing.txt}]", "does not exist"),
        ("python: 3.10\npypi: [httpx]", 'put the version in quotes, such as python: "3.10"'),
    ],
)
def test_errors(tmp_path, text, message):
    (tmp_path / "needs").mkdir()
    (tmp_path / "needs" / "x.yml").write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_needs(tmp_path / "needs")


def test_pinned_files():
    files = versions.pinned_files(Mirrors(), {"compose", "k3s"}, "amd64")
    names = [f["name"] for f in files]
    assert names == [
        "gitea",
        "coredns",
        "gitea-runner",
        "docker",
        "docker-compose",
        "docker-buildx",
        "k3s",
        "k3s-airgap-images",
        "k3s-install",
    ]
    assert "%2B" in next(f["url"] for f in files if f["name"] == "k3s")
    assert versions.pinned_files(Mirrors(github="https://gh.mirror.ir"), {"swarm"}, "amd64")[4]["url"].startswith(
        "https://gh.mirror.ir/docker/compose"
    )
    assert [f["name"] for f in versions.pinned_files(Mirrors(), set(), "amd64")] == ["gitea"]
    needs = add_pinned_files(load_needs(__import__("pathlib").Path("/nonexistent")), files)
    assert [f.name for f in needs.files] == names


def test_an_unquoted_python_version_keeps_working_when_yaml_reads_it_right(tmp_path):
    (tmp_path / "needs").mkdir()
    (tmp_path / "needs" / "x.yml").write_text("python: 3.12\npypi: [httpx]\n")
    assert load_needs(tmp_path / "needs").pypi == {"3.12": ["httpx"]}


def test_read_needs_file_adds_one_file(tmp_path):
    path = tmp_path / "x.yml"
    path.write_text("images: [redis:7]\n")
    needs = Needs(images=["postgres:16"])
    read_needs_file(path, needs)
    assert needs.images == ["postgres:16", "redis:7"] and needs.sources == ["x.yml"]


def test_proxy_package_need_from_servers_yml_or_the_cached_facts():
    proxy = ProxyConfig(host="g", allow=["a.example.com"])
    assert proxy_package_need(ProxyConfig(host="g", allow=["a.example.com"], distro="noble"), None) == ("apt", "noble")
    assert proxy_package_need(proxy, {"id": "rocky", "id_like": "rhel", "version_id": "9.4"}) == ("rpm", "rocky9")
    with pytest.raises(ConfigError, match="outpost hosts --refresh"):
        proxy_package_need(proxy, {"id": "debian"})  # apt, but no codename


def test_all_needs_adds_the_pinned_files_and_tinyproxy(workspace_dir):
    with open(workspace_dir / "servers.yml", "a") as f:
        f.write("egress_proxy:\n  host: gitea-1\n  distro: bookworm\n  allow: [api.example.com]\n")
    needs = all_needs(AirGappedSystem.load(workspace_dir))
    assert "tinyproxy" in needs.apt["bookworm"] and needs.files[-1].name in {
        f["name"] for f in versions.pinned_files(Mirrors(), {"compose"}, "amd64")
    }
