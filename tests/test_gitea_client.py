import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from outpost import http as httpmod
from outpost.gitea import GiteaClient, npm_package_json, pypi_name_version
from outpost.http import Base64File, Http, HttpError, StreamedBody


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
    assert client.packages.upload_generic("docker", "29.9.2", f) == "uploaded"
    assert client.packages.upload_generic("docker", "29.9.2", f) == "exists"
    assert fake_gitea.generic["pkgs/docker/29.9.2/docker-29.9.2.tgz"] == b"binary"


def test_pypi_upload_fields(client, fake_gitea, tmp_path):
    whl = tmp_path / "requests-2.31.0-py3-none-any.whl"
    whl.write_bytes(b"PK\x03\x04wheel")
    assert client.packages.upload_pypi(whl) == "uploaded"
    assert client.packages.upload_pypi(whl) == "exists"
    sent = fake_gitea.pypi[0]
    assert (sent["name"], sent["version"], sent["content"]) == (
        "requests",
        "2.31.0",
        "requests-2.31.0-py3-none-any.whl",
    )
    assert len(sent["sha256_digest"]) == 64
    sdist = tmp_path / "Flask-3.0.0.tar.gz"
    sdist.write_bytes(b"sdist")
    client.packages.upload_pypi(sdist)
    assert fake_gitea.pypi[1]["filetype"] == "sdist"


def test_pypi_and_npm_uploads_stream_the_file_and_send_it_whole(client, fake_gitea, tmp_path, monkeypatch):
    """The wheel and the tarball are read in chunks as they are sent, never whole into memory."""
    whl = tmp_path / "big-1.0-py3-none-any.whl"
    whl.write_bytes(bytes(range(256)) * 5000)
    tgz = make_npm_tgz(tmp_path / "chalk-5.3.0.tgz")
    wheel_bytes, tarball_bytes = whl.read_bytes(), tgz.read_bytes()
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail(f"{self.name} must not be read whole"))
    assert client.packages.upload_pypi(whl) == "uploaded"
    assert client.packages.upload_npm(tgz) == "uploaded"
    sent = fake_gitea.pypi[0]
    assert sent["file_bytes"] == wheel_bytes and sent["sha256_digest"] == hashlib.sha256(wheel_bytes).hexdigest()
    doc = fake_gitea.npm["chalk@5.3.0"]
    assert base64.b64decode(doc["_attachments"]["chalk-5.3.0.tgz"]["data"]) == tarball_bytes
    dist = doc["versions"]["5.3.0"]["dist"]
    assert dist["integrity"] == "sha512-" + base64.b64encode(hashlib.sha512(tarball_bytes).digest()).decode()
    assert dist["shasum"] == hashlib.sha1(tarball_bytes).hexdigest()


@pytest.mark.parametrize("length", range(8))
def test_streamed_body_sends_its_size_and_base64_in_chunks(tmp_path, monkeypatch, length):
    monkeypatch.setattr(httpmod, "BASE64_CHUNK", 3)  # many chunks for a few bytes
    monkeypatch.setattr(httpmod, "CHUNK", 2)
    data = bytes(range(length))
    (tmp_path / "f").write_bytes(data)
    body = StreamedBody([b"head:", tmp_path / "f", b":", Base64File(tmp_path / "f"), b":tail"])
    expected = b"head:" + data + b":" + base64.b64encode(data) + b":tail"
    assert b"".join(body) == expected and body.size == len(expected)


def test_npm_upload_document(client, fake_gitea, tmp_path):
    tgz = make_npm_tgz(tmp_path / "chalk-5.3.0.tgz")
    assert npm_package_json(tgz)["name"] == "chalk"
    assert client.packages.upload_npm(tgz) == "uploaded"
    doc = fake_gitea.npm["chalk@5.3.0"]
    assert doc["dist-tags"] == {"latest": "5.3.0"}
    dist = doc["versions"]["5.3.0"]["dist"]
    assert dist["integrity"].startswith("sha512-") and dist["tarball"].startswith(
        "https://gitea.internal/api/packages/pkgs/npm/chalk/-/5.3.0/"
    )
    assert doc["_attachments"]["chalk-5.3.0.tgz"]["length"] == tgz.stat().st_size
    scoped = make_npm_tgz(tmp_path / "scope-pkg-1.0.0.tgz", "@scope/pkg", "1.0.0")
    client.packages.upload_npm(scoped)
    assert any(path.endswith("/npm/@scope%2Fpkg") for method, path in fake_gitea.requests if method == "PUT")


def test_debian_upload(client, fake_gitea, tmp_path):
    deb = tmp_path / "curl_8.5.0_amd64.deb"
    deb.write_bytes(b"!<arch>\ndebian")
    assert client.packages.upload_debian(deb, "bookworm") == "uploaded"
    assert client.packages.upload_debian(deb, "bookworm") == "exists"
    assert client.packages.debian_has_distro("bookworm") and not client.packages.debian_has_distro("noble")
    assert "PGP" in client.packages.debian_key()


def test_alpine_upload_and_key_name(client, fake_gitea, tmp_path):
    apk = tmp_path / "curl-8.9.1-r1.apk"
    apk.write_bytes(b"\x1f\x8b\x08apk")
    assert client.packages.upload_alpine(apk, "v3.20") == "uploaded"
    assert client.packages.upload_alpine(apk, "v3.20") == "exists"
    assert client.packages.upload_alpine(apk, "v3.19", repository="community") == "uploaded"
    assert [p for m, p in fake_gitea.requests if m == "PUT" and "alpine" in p] == [
        "/api/packages/pkgs/alpine/v3.20/main"
    ] * 2 + ["/api/packages/pkgs/alpine/v3.19/community"]
    assert client.packages.alpine_has_branch("v3.20", "x86_64") and not client.packages.alpine_has_branch(
        "v3.18", "x86_64"
    )
    key = client.packages.alpine_key()
    assert (
        key.name == "pkgs@6f2a9c1e.rsa.pub" and "BEGIN PUBLIC KEY" in key.content
    )  # the name apk needs under /etc/apk/keys
    fake_gitea.alpine_key_name = ""
    assert (
        client.packages.alpine_key().name == "pkgs.rsa.pub"
    )  # documented fallback when Gitea sends no Content-Disposition


def test_rpm_upload_per_group_and_key(client, fake_gitea, tmp_path):
    rpm = tmp_path / "curl-7.76.1-29.el9.x86_64.rpm"
    rpm.write_bytes(b"\xed\xab\xee\xdbrpm")
    assert client.packages.upload_rpm(rpm, group="rocky9") == "uploaded"
    assert client.packages.upload_rpm(rpm, group="rocky9") == "exists"
    assert client.packages.upload_rpm(rpm) == "uploaded"  # no group: Gitea's plain /rpm/upload
    puts = [p for m, p in fake_gitea.requests if m == "PUT" and "/rpm" in p]
    assert puts == ["/api/packages/pkgs/rpm/rocky9/upload"] * 2 + ["/api/packages/pkgs/rpm/upload"]
    assert client.packages.rpm_has_group("rocky9") and not client.packages.rpm_has_group("fedora40")
    assert "PGP" in client.packages.rpm_key()


def test_upload_box_sends_apk_and_rpm_and_records_them_for_the_config_image(client, fake_gitea, tmp_path, ca_pem):
    from outpost import box as boxmod
    from outpost.endpoints import GiteaEndpoint
    from outpost.gitea.upload import upload_box

    box = tmp_path / "box"
    (box / "apk" / "v3.20").mkdir(parents=True)
    (box / "apk" / "v3.20" / "curl-8.9.1-r1.apk").write_bytes(b"apk1")
    (box / "rpm" / "rocky9").mkdir(parents=True)
    (box / "rpm" / "rocky9" / "curl-7.76.1-29.el9.x86_64.rpm").write_bytes(b"rpm1")
    (box / "rpm" / "fedora40").mkdir(parents=True)
    (box / "rpm" / "fedora40" / "curl-8.6.0-8.fc40.x86_64.rpm").write_bytes(b"rpm2")
    boxmod.write_manifest(box)
    assert (
        boxmod.distros_in(box) == []
        and boxmod.distros_in(box, "apk") == ["v3.20"]
        and boxmod.distros_in(box, "rpm") == ["fedora40", "rocky9"]
    )
    stats = upload_box(
        box,
        client,
        GiteaEndpoint(name="gitea.internal", ip="10.0.0.5", org="pkgs"),
        ca_pem,
        "amd64",
        log=lambda *_: None,
    )
    assert stats["apk"] == {"uploaded": 1, "exists": 0} and stats["rpm"] == {"uploaded": 2, "exists": 0}
    assert stats["config_image"] == {"distros": [], "apk_branches": ["v3.20"], "rpm_distros": ["fedora40", "rocky9"]}
    assert ("GET", "/api/packages/pkgs/alpine/key") in fake_gitea.requests and (
        "GET",
        "/api/packages/pkgs/rpm/repository.key",
    ) in fake_gitea.requests
    assert ("GET", "/api/packages/pkgs/debian/repository.key") not in fake_gitea.requests  # no apt distros, no apt key
    assert ("pkgs/config", "latest") in fake_gitea.manifests


def test_upload_puts_every_image_under_the_organisation(client, fake_gitea, tmp_path, ca_pem):
    """python:3.12-slim lands as pkgs/library/python, bitnami/redis as pkgs/bitnami/redis; no other org is made."""
    from outpost import box as boxmod
    from outpost import oci
    from outpost.endpoints import GiteaEndpoint
    from outpost.gitea.upload import upload_box

    box = tmp_path / "box"
    for ref in ("python:3.12-slim", "bitnami/redis:7", "ghcr.io/org/app:1.4"):
        r = oci.parse_ref(ref)
        oci.build_layout({"/hello": (ref.encode(), 0o644)}, box / "images" / r.safe_name, r.short, "amd64")
    boxmod.write_manifest(box)
    gitea = GiteaEndpoint(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    lines = []
    stats = upload_box(box, client, gitea, ca_pem, "amd64", log=lines.append)
    assert stats["images"] == {"uploaded": 3, "exists": 0}
    stored = {repo for repo, _tag in fake_gitea.manifests}
    assert stored == {"pkgs/library/python", "pkgs/bitnami/redis", "pkgs/org/app", "pkgs/config"}
    assert ("pkgs/library/python", "3.12-slim") in fake_gitea.manifests and (
        "pkgs/org/app",
        "1.4",
    ) in fake_gitea.manifests
    assert fake_gitea.orgs == {"pkgs"}, "no organisation per image owner"
    assert not any(path.startswith("/v2/library/") for _m, path in fake_gitea.requests)
    assert "    python:3.12-slim  ->  gitea.internal/pkgs/library/python:3.12-slim" in lines
    assert "    bitnami/redis:7  ->  gitea.internal/pkgs/bitnami/redis:7" in lines


def test_error_surfaces_server_text(fake_gitea, tmp_path):
    client = GiteaClient(Http(fake_gitea.url), "pkgs")
    with pytest.raises(ValueError, match="not a wheel"):
        pypi_name_version("x.rpm")
    with pytest.raises(HttpError, match="HTTP 404"):
        client.http.get("/nothing", ok=(200,))


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("requests-2.31.0-py3-none-any.whl", ("requests", "2.31.0")),
        ("psycopg_binary-3.2.3-cp312-cp312-manylinux_2_17_x86_64.whl", ("psycopg_binary", "3.2.3")),
        ("Flask-3.0.0.tar.gz", ("Flask", "3.0.0")),
        ("some_pkg-1.0.0rc1.zip", ("some_pkg", "1.0.0rc1")),
    ],
)
def test_pypi_name_version(filename, expected):
    assert pypi_name_version(filename) == expected


def test_an_image_gitea_already_has_counts_as_already_there(client, fake_gitea, tmp_path, ca_pem):
    """Uploading the same box twice: the images are there already, so the second upload counts no new image."""
    from outpost import box as boxmod
    from outpost import oci
    from outpost.endpoints import GiteaEndpoint
    from outpost.gitea.upload import upload_box

    box = tmp_path / "box"
    ref = oci.parse_ref("python:3.12-slim")
    oci.build_layout({"/hello": (b"hi", 0o644)}, box / "images" / ref.safe_name, ref.short, "amd64")
    boxmod.write_manifest(box)
    gitea = GiteaEndpoint(name="gitea.internal", ip="10.0.0.5", org="pkgs")
    lines = []
    assert upload_box(box, client, gitea, ca_pem, "amd64", log=lambda *_: None)["images"] == {
        "uploaded": 1,
        "exists": 0,
    }
    assert upload_box(box, client, gitea, ca_pem, "amd64", log=lines.append)["images"] == {"uploaded": 0, "exists": 1}
    assert "    images: 0 uploaded, 1 already there" in lines


def test_file_uploads_stream_from_disk(client, fake_gitea, tmp_path, monkeypatch):
    """generic, Debian, Alpine and RPM uploads hand Http the file, which streams it, instead of its bytes."""
    sent = []
    request = client.http.request
    monkeypatch.setattr(
        client.http,
        "request",
        lambda method, path, data=None, **kw: sent.append(data) or request(method, path, data=data, **kw),
    )
    package = tmp_path / "pkg-1.0"
    package.write_bytes(b"package bytes")
    client.packages.upload_generic("tool", "1.0", package)
    client.packages.upload_debian(package, "bookworm")
    client.packages.upload_alpine(package, "v3.20")
    client.packages.upload_rpm(package, group="rocky9")
    assert sent == [package] * 4
    assert fake_gitea.generic["pkgs/tool/1.0/pkg-1.0"] == b"package bytes"


def test_every_kind_in_the_box_has_an_uploader():
    from outpost import box as boxmod
    from outpost.gitea.upload import KIND_UPLOADERS, Tally

    assert set(KIND_UPLOADERS) == set(boxmod.KINDS)
    tally = Tally()
    tally.add("pypi", "uploaded")
    tally.add("pypi", "exists")
    tally.add("pypi", "exists")
    assert tally.counts() == {"pypi": {"uploaded": 1, "exists": 2}}
