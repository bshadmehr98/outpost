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


def test_alpine_upload_and_key_name(client, fake_gitea, tmp_path):
    apk = tmp_path / "curl-8.9.1-r1.apk"
    apk.write_bytes(b"\x1f\x8b\x08apk")
    assert client.upload_alpine(apk, "v3.20") == "uploaded"
    assert client.upload_alpine(apk, "v3.20") == "exists"
    assert client.upload_alpine(apk, "v3.19", repository="community") == "uploaded"
    assert [p for m, p in fake_gitea.requests if m == "PUT" and "alpine" in p] == ["/api/packages/pkgs/alpine/v3.20/main"] * 2 + ["/api/packages/pkgs/alpine/v3.19/community"]
    assert client.alpine_has_branch("v3.20", "x86_64") and not client.alpine_has_branch("v3.18", "x86_64")
    key = client.alpine_key()
    assert key.name == "pkgs@6f2a9c1e.rsa.pub" and "BEGIN PUBLIC KEY" in key.content   # the name apk needs under /etc/apk/keys
    fake_gitea.alpine_key_name = ""
    assert client.alpine_key().name == "pkgs.rsa.pub"   # documented fallback when Gitea sends no Content-Disposition


def test_rpm_upload_per_group_and_key(client, fake_gitea, tmp_path):
    rpm = tmp_path / "curl-7.76.1-29.el9.x86_64.rpm"
    rpm.write_bytes(b"\xed\xab\xee\xdbrpm")
    assert client.upload_rpm(rpm, group="rocky9") == "uploaded"
    assert client.upload_rpm(rpm, group="rocky9") == "exists"
    assert client.upload_rpm(rpm) == "uploaded"   # no group: Gitea's plain /rpm/upload
    puts = [p for m, p in fake_gitea.requests if m == "PUT" and "/rpm" in p]
    assert puts == ["/api/packages/pkgs/rpm/rocky9/upload"] * 2 + ["/api/packages/pkgs/rpm/upload"]
    assert client.rpm_has_group("rocky9") and not client.rpm_has_group("fedora40")
    assert "PGP" in client.rpm_key()


def test_upload_box_sends_apk_and_rpm_and_records_them_for_the_config_image(client, fake_gitea, tmp_path):
    from outpost import box as boxmod
    from outpost.gitea.upload import upload_box
    from outpost.infra.clients import GiteaInfo
    box = tmp_path / "box"
    (box / "apk" / "v3.20").mkdir(parents=True)
    (box / "apk" / "v3.20" / "curl-8.9.1-r1.apk").write_bytes(b"apk1")
    (box / "rpm" / "rocky9").mkdir(parents=True)
    (box / "rpm" / "rocky9" / "curl-7.76.1-29.el9.x86_64.rpm").write_bytes(b"rpm1")
    (box / "rpm" / "fedora40").mkdir(parents=True)
    (box / "rpm" / "fedora40" / "curl-8.6.0-8.fc40.x86_64.rpm").write_bytes(b"rpm2")
    boxmod.write_manifest(box)
    assert boxmod.distros_in(box) == [] and boxmod.distros_in(box, "apk") == ["v3.20"] and boxmod.distros_in(box, "rpm") == ["fedora40", "rocky9"]
    stats = upload_box(box, client, GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs"), "CA PEM", log=lambda *_: None)
    assert stats["apk"] == {"uploaded": 1, "exists": 0} and stats["rpm"] == {"uploaded": 2, "exists": 0}
    assert stats["config_image"] == {"distros": [], "apk_branches": ["v3.20"], "rpm_distros": ["fedora40", "rocky9"]}
    assert ("GET", "/api/packages/pkgs/alpine/key") in fake_gitea.requests and ("GET", "/api/packages/pkgs/rpm/repository.key") in fake_gitea.requests
    assert ("GET", "/api/packages/pkgs/debian/repository.key") not in fake_gitea.requests   # no apt distros, no apt key
    assert ("pkgs/config", "latest") in fake_gitea.manifests


def test_upload_puts_every_image_under_the_organisation(client, fake_gitea, tmp_path):
    """python:3.12-slim lands as pkgs/library--python, bitnami/redis as pkgs/bitnami--redis; no other org is made."""
    from outpost import box as boxmod
    from outpost import oci
    from outpost.gitea.upload import upload_box
    from outpost.infra.clients import GiteaInfo
    box = tmp_path / "box"
    for ref in ("python:3.12-slim", "bitnami/redis:7", "ghcr.io/org/app:1.4"):
        r = oci.parse_ref(ref)
        oci.build_layout({"/hello": (ref.encode(), 0o644)}, box / "images" / r.safe_name, r.short)
    boxmod.write_manifest(box)
    gitea = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    lines = []
    stats = upload_box(box, client, gitea, "CA PEM", log=lines.append)
    assert stats["images"] == {"uploaded": 3, "exists": 0}
    stored = {repo for repo, _tag in fake_gitea.manifests}
    assert stored == {"pkgs/library--python", "pkgs/bitnami--redis", "pkgs/org--app", "pkgs/config"}
    assert ("pkgs/library--python", "3.12-slim") in fake_gitea.manifests and ("pkgs/org--app", "1.4") in fake_gitea.manifests
    assert fake_gitea.orgs == {"pkgs"}, "no organisation per image owner"
    assert not any(path.startswith("/v2/library/") for _m, path in fake_gitea.requests)
    assert "    python:3.12-slim  ->  gitea.internal/pkgs/library--python:3.12-slim" in lines
    assert "    bitnami/redis:7  ->  gitea.internal/pkgs/bitnami--redis:7" in lines


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
