"""Talk to a real Gitea in Docker and to real public registries. Run with:

    OUTPOST_INTEGRATION=1 pytest tests/integration -v

Needs Docker and internet. Starts gitea/gitea:28.2.0 in a container, reached by its bridge IP so that
both this machine and a Docker-in-Docker daemon (used to test the registry-mirror path) can use the
same URL. Containers are removed afterwards.
"""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from outpost import oci, versions
from outpost.gitea.client import GiteaClient
from outpost.http import Http
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


def test_pull_from_docker_hub_and_push_to_gitea(gitea, tmp_path):
    ref = oci.parse_ref("alpine:3.20")
    layout = oci.Registry(Http("https://registry-1.docker.io")).pull(ref, tmp_path / "alpine")
    assert oci.layout_ref(layout) == "alpine:3.20"
    gitea.ensure_org("library")
    gitea.push_image(layout, "library", "alpine", "3.20")
    gitea.push_image(layout, "library", "alpine", "outpost-only")  # a tag Docker Hub does not have
    assert ("library/alpine", "outpost-only") not in {}  # pulled back through a real Docker daemon in the next test


def test_docker_daemon_uses_gitea_as_mirror(gitea):
    """A Docker daemon with Gitea as registry mirror pulls `alpine:outpost-only`, a tag that only exists in Gitea."""
    host = URL.split("//")[1]
    sh("docker", "rm", "-f", DIND, check=False)
    sh("docker", "run", "-d", "--privileged", "--name", DIND, "--network", NET, "-e", "DOCKER_TLS_CERTDIR=", "docker:dind",
       f"--registry-mirror={URL}", f"--insecure-registry={host}")
    for _ in range(60):
        if sh("docker", "exec", DIND, "docker", "info", check=False).returncode == 0:
            break
        time.sleep(2)
    pulled = sh("docker", "exec", DIND, "docker", "pull", "alpine:outpost-only", check=False)
    assert pulled.returncode == 0, pulled.stderr
    out = sh("docker", "exec", DIND, "docker", "run", "--rm", "alpine:outpost-only", "cat", "/etc/alpine-release").stdout
    assert out.startswith("3.20")


def test_config_image_is_usable_in_a_build(gitea, tmp_path):
    host = URL.split("//")[1]
    info = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    files = clients.config_image_files(info, "CA PEM", "APT KEY", ["bookworm"])
    layout = oci.build_layout(files, tmp_path / "config", info.config_image)
    gitea.push_image(layout, "pkgs", "config", "latest")
    (tmp_path / "ctx").mkdir()
    (tmp_path / "ctx" / "Dockerfile").write_text(
        f"FROM {host}/pkgs/config:latest AS config\nFROM {host}/library/alpine:3.20\nCOPY --from=config / /\n"
        "CMD cat /etc/pip.conf /etc/apt/sources.list.d/outpost.list\n")
    # build inside dind, whose daemon trusts the plain-http Gitea and mirrors from it
    sh("docker", "cp", str(tmp_path / "ctx"), f"{DIND}:/ctx")
    sh("docker", "exec", DIND, "docker", "build", "-q", "-t", "outpost-it-config", "/ctx")
    out = sh("docker", "exec", DIND, "docker", "run", "--rm", "outpost-it-config").stdout
    assert "index-url = https://gitea.internal/api/packages/pkgs/pypi/simple" in out
    assert "debian bookworm main" in out
