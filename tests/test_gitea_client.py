import io
import json
import tarfile

import pytest

from outpost.gitea.client import GiteaClient, npm_package_json, pypi_name_version
from outpost.http import Http, HttpError


def make_npm_tgz(path, name="chalk", version="5.3.0"):
    with tarfile.open(path, "w:gz") as tar:
        data = json.dumps({"name": name, "version": version, "description": "colors"}).encode()
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def client(fake_gitea):
    return GiteaClient(Http(fake_gitea.url, basic=("outpost", "t")), "pkgs", public_url="https://gitea.internal")


def test_version_and_org(client, fake_gitea):
    assert client.version() == "28.2.0"
    assert client.ensure_org("library") is True
    assert client.ensure_org("library") is False
    assert "library" in fake_gitea.orgs


def test_generic_upload_and_exists(client, fake_gitea, tmp_path):
    f = tmp_path / "docker-29.9.2.tgz"
    f.write_bytes(b"binary")
    assert client.upload_generic("docker", "29.9.2", f) == "uploaded"
    assert client.upload_generic("docker", "29.9.2", f) == "exists"
    assert fake_gitea.generic["pkgs/docker/29.9.2/docker-29.9.2.tgz"] == b"binary"


def test_pypi_upload_fields(client, fake_gitea, tmp_path):
    whl = tmp_path / "requests-2.31.0-py3-none-any.whl"
    whl.write_bytes(b"PK\x03\x04wheel")
    assert client.upload_pypi(whl) == "uploaded"
    assert client.upload_pypi(whl) == "exists"
    sent = fake_gitea.pypi[0]
    assert (sent["name"], sent["version"], sent["content"]) == ("requests", "2.31.0", "requests-2.31.0-py3-none-any.whl")
    assert len(sent["sha256_digest"]) == 64
    sdist = tmp_path / "Flask-3.0.0.tar.gz"
    sdist.write_bytes(b"sdist")
    client.upload_pypi(sdist)
    assert fake_gitea.pypi[1]["filetype"] == "sdist"


def test_npm_upload_document(client, fake_gitea, tmp_path):
    tgz = make_npm_tgz(tmp_path / "chalk-5.3.0.tgz")
    assert npm_package_json(tgz)["name"] == "chalk"
    assert client.upload_npm(tgz) == "uploaded"
    doc = fake_gitea.npm["chalk@5.3.0"]
    assert doc["dist-tags"] == {"latest": "5.3.0"}
    dist = doc["versions"]["5.3.0"]["dist"]
    assert dist["integrity"].startswith("sha512-") and dist["tarball"].startswith("https://gitea.internal/api/packages/pkgs/npm/chalk/-/5.3.0/")
    assert doc["_attachments"]["chalk-5.3.0.tgz"]["length"] == tgz.stat().st_size
    scoped = make_npm_tgz(tmp_path / "scope-pkg-1.0.0.tgz", "@scope/pkg", "1.0.0")
    client.upload_npm(scoped)
    assert any(path.endswith("/npm/@scope%2Fpkg") for method, path in fake_gitea.requests if method == "PUT")


def test_debian_upload(client, fake_gitea, tmp_path):
    deb = tmp_path / "curl_8.5.0_amd64.deb"
    deb.write_bytes(b"!<arch>\ndebian")
    assert client.upload_debian(deb, "bookworm") == "uploaded"
    assert client.upload_debian(deb, "bookworm") == "exists"
    assert client.debian_has_distro("bookworm") and not client.debian_has_distro("noble")
    assert "PGP" in client.debian_key()


def test_error_surfaces_server_text(fake_gitea, tmp_path):
    client = GiteaClient(Http(fake_gitea.url), "pkgs")
    with pytest.raises(ValueError, match="not a wheel"):
        pypi_name_version("x.rpm")
    with pytest.raises(HttpError, match="HTTP 404"):
        client.http.get("/nothing", ok=(200,))


@pytest.mark.parametrize("filename,expected", [
    ("requests-2.31.0-py3-none-any.whl", ("requests", "2.31.0")),
    ("psycopg_binary-3.2.3-cp312-cp312-manylinux_2_17_x86_64.whl", ("psycopg_binary", "3.2.3")),
    ("Flask-3.0.0.tar.gz", ("Flask", "3.0.0")),
    ("some_pkg-1.0.0rc1.zip", ("some_pkg", "1.0.0rc1")),
])
def test_pypi_name_version(filename, expected):
    assert pypi_name_version(filename) == expected
