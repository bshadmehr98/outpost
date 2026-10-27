"""Talk to a real Gitea in Docker and to real public registries. Run with:

    OUTPOST_INTEGRATION=1 pytest tests/integration -v

Needs Docker and internet. Starts gitea/gitea:28.2.0 in a container, reached by its bridge IP so that
both this machine and a Docker-in-Docker daemon (used to pull an image by its Gitea name) can use the
same URL. Containers are removed afterwards.
"""

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from outpost import oci, versions
from outpost.config import Mirrors
from outpost.fetch import apk as apkmod
from outpost.gitea.client import GiteaClient
from outpost.http import Http, HttpError
from outpost.infra import clients
from outpost.infra.clients import GiteaInfo

pytestmark = pytest.mark.skipif(os.environ.get("OUTPOST_INTEGRATION") != "1", reason="set OUTPOST_INTEGRATION=1")
pytestmark = [pytestmark, pytest.mark.integration]

NAME = "outpost-it-gitea"
DIND = "outpost-it-dind"
NET = "outpost-it-net"
SUBNET = "172.31.250.0/24"
GITEA_IP = "172.31.250.10"
PORT = 3000
URL = ""  # set by the fixture


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kw)


@pytest.fixture(scope="module")
def gitea():
    global URL
    sh("docker", "rm", "-f", NAME, DIND, check=False)
    sh("docker", "network", "rm", NET, check=False)
    sh("docker", "network", "create", "--subnet", SUBNET, NET)
    URL = f"http://{GITEA_IP}:{PORT}"
    # ROOT_URL must be reachable by every client, including the token realm Docker follows
    sh("docker", "run", "-d", "--name", NAME, "--network", NET, "--ip", GITEA_IP,
       "-e", "GITEA__security__INSTALL_LOCK=true", "-e", "GITEA__packages__ENABLED=true",
       "-e", "GITEA__service__DISABLE_REGISTRATION=true", "-e", f"GITEA__server__ROOT_URL={URL}/",
       f"gitea/gitea:{versions.GITEA}")
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{URL}/api/v1/version", timeout=2)
            break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError(sh("docker", "logs", NAME, check=False).stdout[-2000:])
    sh("docker", "exec", "-u", "git", NAME, "gitea", "admin", "user", "create", "--admin", "--username", "outpost",
       "--password", "Outpost123!", "--email", "outpost@example.com", "--must-change-password=false")
    token = sh("docker", "exec", "-u", "git", NAME, "gitea", "admin", "user", "generate-access-token",
               "--username", "outpost", "--token-name", "it", "--scopes", "all", "--raw").stdout.strip().splitlines()[-1]
    client = GiteaClient(Http(URL, basic=("outpost", token)), "pkgs", public_url=URL)
    client.ensure_org("pkgs")
    yield client
    sh("docker", "rm", "-f", NAME, DIND, check=False)
    sh("docker", "network", "rm", NET, check=False)


def fetch(url: str, dest: Path) -> Path:
    Http().download(url, dest, quiet=True)
    return dest


def test_version(gitea):
    assert gitea.version().startswith(versions.GITEA.split(".")[0])


def test_generic(gitea, tmp_path):
    f = tmp_path / "tool"
    f.write_bytes(b"#!/bin/sh\necho hi\n")
    assert gitea.upload_generic("tool", "1.0", f) == "uploaded"
    assert gitea.upload_generic("tool", "1.0", f) == "exists"
    assert urllib.request.urlopen(f"{URL}/api/packages/pkgs/generic/tool/1.0/tool").read() == f.read_bytes()


def test_pypi(gitea, tmp_path):
    meta = json.loads(urllib.request.urlopen("https://pypi.org/pypi/six/1.16.0/json").read())
    wheel_url = next(u["url"] for u in meta["urls"] if u["filename"].endswith(".whl"))
    whl = fetch(wheel_url, tmp_path / wheel_url.rsplit("/", 1)[1])
    assert gitea.upload_pypi(whl) == "uploaded"
    assert gitea.upload_pypi(whl) == "exists"
    simple = urllib.request.urlopen(f"{URL}/api/packages/pkgs/pypi/simple/six/").read().decode()
    assert whl.name in simple


def test_npm(gitea, tmp_path):
    tgz = fetch("https://registry.npmjs.org/chalk/-/chalk-5.3.0.tgz", tmp_path / "chalk-5.3.0.tgz")
    assert gitea.upload_npm(tgz) == "uploaded"
    assert gitea.upload_npm(tgz) == "exists"
    doc = json.loads(urllib.request.urlopen(f"{URL}/api/packages/pkgs/npm/chalk").read())
    assert doc["dist-tags"]["latest"] == "5.3.0"
    served = urllib.request.urlopen(doc["versions"]["5.3.0"]["dist"]["tarball"]).read()
    assert served == tgz.read_bytes()


def test_debian(gitea, tmp_path):
    deb = fetch("http://deb.debian.org/debian/pool/main/h/hello/hello_2.10-3_amd64.deb", tmp_path / "hello_2.10-3_amd64.deb")
    assert gitea.upload_debian(deb, "bookworm") == "uploaded"
    assert gitea.upload_debian(deb, "bookworm") == "exists"
    assert gitea.debian_has_distro("bookworm") and not gitea.debian_has_distro("noble")
    assert "BEGIN PGP PUBLIC KEY BLOCK" in gitea.debian_key()


@pytest.fixture(scope="module")
def apk_files(tmp_path_factory):
    """zlib and its dependency tree for Alpine v3.20, fetched the way `outpost fetch` does (apk inside alpine:3.20)."""
    box = tmp_path_factory.mktemp("box")
    apkmod.fetch({"v3.20": ["zlib"]}, box, Mirrors(), log=lambda *_: None)
    files = sorted((box / "apk" / "v3.20").glob("*.apk"))
    assert files and any(f.name.startswith("zlib-") for f in files)
    return files


def test_alpine(gitea, apk_files):
    """Real .apk files land in Gitea's Alpine registry; Gitea signs the index with a key named after the owner and
    a fingerprint, reported in Content-Disposition, and serves an APKINDEX for the branch and architecture."""
    first = apk_files[0]
    assert gitea.upload_alpine(first, "v3.20") == "uploaded"
    assert gitea.upload_alpine(first, "v3.20") == "exists"
    for path in apk_files[1:]:
        gitea.upload_alpine(path, "v3.20")
    key = gitea.alpine_key()
    assert key.name.startswith("pkgs@") and key.name.endswith(".rsa.pub") and "BEGIN PUBLIC KEY" in key.content
    assert gitea.alpine_has_branch("v3.20", "x86_64") and not gitea.alpine_has_branch("v3.19", "x86_64")
    index = urllib.request.urlopen(f"{URL}/api/packages/pkgs/alpine/v3.20/main/x86_64/APKINDEX.tar.gz").read()
    assert index[:2] == b"\x1f\x8b"


@pytest.mark.parametrize("ref", ["python:3.12-slim", "bitnami/redis:7", "ghcr.io/my-org/team/app:1.4",
                                 "quay.io/my_org/my.app:1"])
def test_generated_image_names_are_accepted_by_gitea(gitea, tmp_path, ref):
    """Every name Outpost generates must be one real Gitea accepts. The OCI spec is not enough: `library--python`
    is valid OCI and Gitea answers NAME_INVALID. Push a tiny image under the generated name, pull it back, and
    find it listed as one container package of the organisation."""
    r = oci.parse_ref(ref)
    layout = oci.build_layout({"/hello": (ref.encode(), 0o644)}, tmp_path / "img", r.short)
    gitea.push_image(layout, "pkgs", r.mirror_name, r.tag)
    pulled = gitea.registry().pull(oci.Ref(registry="gitea", repo=f"pkgs/{r.mirror_name}", tag=r.tag), tmp_path / "back")
    assert json.loads((pulled / "index.json").read_text())["manifests"]
    packages = gitea.http.get("/api/v1/packages/pkgs?type=container&limit=50").json()
    assert r.mirror_name in {p["name"] for p in packages}


def test_gitea_rejects_a_double_hyphen(gitea, tmp_path):
    """The name of the first cut, kept as a record of why the nested path was chosen."""
    layout = oci.build_layout({"/hello": (b"x", 0o644)}, tmp_path / "img", "x:1")
    with pytest.raises(HttpError, match="NAME_INVALID"):
        gitea.push_image(layout, "pkgs", "library--python", "1")


def test_pull_from_docker_hub_and_push_to_gitea(gitea, tmp_path):
    """`alpine:3.20` from Docker Hub lands under the one organisation as `pkgs/library/alpine`, like every image."""
    ref = oci.parse_ref("alpine:3.20")
    layout = oci.Registry(Http("https://registry-1.docker.io")).pull(ref, tmp_path / "alpine")
    assert oci.layout_ref(layout) == "alpine:3.20"
    assert ref.mirror_name == "library/alpine"
    gitea.push_image(layout, "pkgs", ref.mirror_name, "3.20")
    gitea.push_image(layout, "pkgs", ref.mirror_name, "outpost-only")  # a tag Docker Hub does not have
    listed = gitea.registry().call("GET", "/v2/pkgs/library/alpine/tags/list", "pkgs/library/alpine")
    assert listed.status == 200 and {"3.20", "outpost-only"} <= set(listed.json()["tags"])
    assert gitea.http.get("/api/v1/orgs/library").status == 404  # no organisation per image owner any more


def test_docker_daemon_pulls_by_gitea_name(gitea):
    """A Docker daemon with no registry mirror pulls `<gitea>/pkgs/library/alpine:outpost-only` by its explicit
    name, a tag that only exists in Gitea. A bare `alpine:outpost-only` is not rewritten and must fail."""
    host = URL.split("//")[1]
    sh("docker", "rm", "-f", DIND, check=False)
    sh("docker", "run", "-d", "--privileged", "--name", DIND, "--network", NET, "-e", "DOCKER_TLS_CERTDIR=", "docker:dind",
       f"--insecure-registry={host}")
    for _ in range(60):
        if sh("docker", "exec", DIND, "docker", "info", check=False).returncode == 0:
            break
        time.sleep(2)
    assert "Registry Mirrors" not in sh("docker", "exec", DIND, "docker", "info").stdout
    pulled = sh("docker", "exec", DIND, "docker", "pull", f"{host}/pkgs/library/alpine:outpost-only", check=False)
    assert pulled.returncode == 0, pulled.stderr
    out = sh("docker", "exec", DIND, "docker", "run", "--rm", f"{host}/pkgs/library/alpine:outpost-only", "cat", "/etc/alpine-release").stdout
    assert out.startswith("3.20")
    assert sh("docker", "exec", DIND, "docker", "pull", "alpine:outpost-only", check=False).returncode != 0


def test_config_image_is_usable_in_a_build(gitea, tmp_path):
    host = URL.split("//")[1]
    info = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    files = clients.config_image_files(info, "CA PEM", "APT KEY", ["bookworm"])
    layout = oci.build_layout(files, tmp_path / "config", info.config_image)
    gitea.push_image(layout, "pkgs", "config", "latest")
    (tmp_path / "ctx").mkdir()
    (tmp_path / "ctx" / "Dockerfile").write_text(
        f"FROM {host}/pkgs/config:latest AS config\nFROM {host}/pkgs/library/alpine:3.20\nCOPY --from=config / /\n"
        "CMD cat /etc/pip.conf /etc/apt/sources.list.d/outpost.list\n")
    # build inside dind, whose daemon trusts the plain-http Gitea; both images are named by their Gitea copy
    sh("docker", "cp", str(tmp_path / "ctx"), f"{DIND}:/ctx")
    sh("docker", "exec", DIND, "docker", "build", "-q", "-t", "outpost-it-config", "/ctx")
    out = sh("docker", "exec", DIND, "docker", "run", "--rm", "outpost-it-config").stdout
    assert "index-url = https://gitea.internal/api/packages/pkgs/pypi/simple" in out
    assert "debian bookworm main" in out


def test_apk_add_from_gitea_with_the_config_image_key(gitea, apk_files):
    """An Alpine build that copies the config image in finds Gitea's key under /etc/apk/keys/<name Gitea signed
    with>, so `apk add` from a repositories file that names only Gitea works. The repositories line is written
    with the plain-http URL this test's Gitea answers on; on a real platform client_files writes the https one."""
    host = URL.split("//")[1]
    info = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    files = clients.config_image_files(info, "CA PEM", None, [], apk_key=gitea.alpine_key(), apk_branches=["v3.20"])
    key_paths = [p for p in files if p.startswith("/etc/apk/keys/")]
    assert len(key_paths) == 1 and files["/etc/apk/repositories"][0] == b"https://gitea.internal/api/packages/pkgs/alpine/v3.20/main\n"
    layout = oci.build_layout(files, Path(tempfile_dir := tempfile.mkdtemp(prefix="outpost-it-apk-")) / "config", info.config_image)
    gitea.push_image(layout, "pkgs", "config", "apk-test")
    ctx = Path(tempfile_dir) / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        f"FROM {host}/pkgs/config:apk-test AS config\nFROM {host}/pkgs/library/alpine:3.20\nCOPY --from=config / /\n"
        f"RUN echo http://{host}/api/packages/pkgs/alpine/v3.20/main > /etc/apk/repositories && apk update && apk add zlib\n"
        "CMD apk info zlib && ls /etc/apk/keys\n")
    sh("docker", "cp", str(ctx), f"{DIND}:/ctx-apk")
    built = sh("docker", "exec", DIND, "docker", "build", "-q", "-t", "outpost-it-apk", "/ctx-apk", check=False)
    assert built.returncode == 0, built.stderr[-2000:]
    out = sh("docker", "exec", DIND, "docker", "run", "--rm", "outpost-it-apk").stdout
    assert "zlib-" in out and key_paths[0].rsplit("/", 1)[1] in out
