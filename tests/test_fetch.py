import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from outpost import oci, versions
from outpost.config import Mirrors
from outpost.fetch import apt, files, images, npm, pypi
from outpost.needs import FileNeed, NpmNeed


class Recorder:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = 1 if self.fail_first and len(self.calls) == 1 else 0
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": "ERROR: No matching distribution"})()


def test_pypi_command_flags(tmp_path):
    run = Recorder()
    result = pypi.fetch({"3.12": ["flask==3.0.0", "httpx"]}, tmp_path, Mirrors(pypi="https://pypi.mirror.ir/simple"), log=lambda *_: None, run=run)
    cmd = run.calls[0]
    assert cmd[:3] == ["pip", "download", "-q"]
    assert "--index-url" in cmd and "https://pypi.mirror.ir/simple" in cmd
    assert cmd[cmd.index("--python-version") + 1] == "3.12" and "cp312" in cmd and "--only-binary=:all:" in cmd
    assert cmd.count("--platform") == len(versions.names()["pip"]) and "manylinux2014_x86_64" in cmd
    assert (tmp_path / "pypi" / ".requirements-py3.12.txt").read_text() == "flask==3.0.0\nhttpx\n"
    assert result["failed"] == []


def test_pypi_falls_back_per_package(tmp_path):
    run = Recorder(fail_first=True)
    pypi.fetch({"3.11": ["a", "b"]}, tmp_path, Mirrors(), log=lambda *_: None, run=run)
    assert len(run.calls) == 3  # one batch attempt, then one wheel attempt per package (both succeed)
    assert "a" in run.calls[1] and "--only-binary=:all:" in run.calls[1] and "b" in run.calls[2]


def test_files_rewrite_github_mirror():
    assert files.rewrite("https://github.com/a/b/releases/x", Mirrors(github="https://gh.ir")) == "https://gh.ir/a/b/releases/x"
    assert files.rewrite("https://dl.gitea.com/x", Mirrors(github="https://gh.ir")) == "https://dl.gitea.com/x"


def test_apt_script_and_image():
    assert apt.image_for("bookworm") == "debian:bookworm" and apt.image_for("noble") == "ubuntu:noble"
    with pytest.raises(ValueError):
        apt.image_for("windows")
    script = apt.script_for("bookworm", ["curl", "libpq-dev"], Mirrors(debian="http://deb.mirror.ir/debian"))
    assert "deb http://deb.mirror.ir/debian bookworm main" in script
    assert "apt-cache depends --recurse --no-recommends" in script and "curl libpq-dev" in script and "apt-get download" in script
    run = Recorder()
    apt.fetch({"noble": ["htop"]}, Path("/tmp/box-test"), Mirrors(), log=lambda *_: None, run=run)
    cmd = run.calls[0]
    assert cmd[:4] == ["docker", "run", "--rm", "--platform"] and "ubuntu:noble" in cmd


class _NpmHandler(BaseHTTPRequestHandler):
    tgz = None

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/chalk/5.3.0":
            body = json.dumps({"dist": {"tarball": f"http://{self.headers['Host']}/chalk/-/chalk-5.3.0.tgz", "integrity": None}}).encode()
        elif self.path.endswith(".tgz"):
            body = self.tgz
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def npm_server():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b'{"name":"chalk","version":"5.3.0"}'
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    handler = type("H", (_NpmHandler,), {"tgz": buf.getvalue()})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", buf.getvalue()
    server.shutdown()


def test_npm_fetch_from_metadata_and_lockfile_url(tmp_path, npm_server):
    url, tgz = npm_server
    import base64
    import hashlib
    good = "sha512-" + base64.b64encode(hashlib.sha512(tgz).digest()).decode()
    needs = [
        NpmNeed("chalk", "5.3.0"),
        NpmNeed("other", "1.0.0", resolved=f"{url}/other/-/other-1.0.0.tgz", integrity=good),
        NpmNeed("bad", "1.0.0", resolved=f"{url}/bad/-/bad-1.0.0.tgz", integrity="sha512-AAAA"),
    ]
    result = npm.fetch(needs, tmp_path, Mirrors(npm=url), log=lambda *_: None)
    assert result["downloaded"] == 2 and len(result["failed"]) == 1 and "integrity" in result["failed"][0]
    assert sorted(p.name for p in (tmp_path / "npm").iterdir()) == ["chalk-5.3.0.tgz", "other-1.0.0.tgz"]
    assert npm.tarball_name("@scope/pkg", "1.0.0") == "scope-pkg-1.0.0.tgz"


def test_images_fetch_uses_docker_mirror(tmp_path, fake_gitea):
    src = oci.build_layout({"/a": (b"a", 0o644)}, tmp_path / "src", "alpine:3.20")
    fake_gitea.add_layout(src, "library/alpine", "3.20")
    result = images.fetch(["alpine:3.20"], tmp_path / "box", Mirrors(docker=fake_gitea.url), log=lambda *_: None)
    assert result["pulled"] == 1
    assert oci.layout_ref(tmp_path / "box" / "images" / "alpine_3.20") == "alpine:3.20"
    assert images.fetch(["alpine:3.20"], tmp_path / "box", Mirrors(docker=fake_gitea.url), log=lambda *_: None)["skipped"] == 1


def test_files_fetch_skips_existing(tmp_path):
    need = FileNeed("tool", "1.0", "http://127.0.0.1:9/never", "tool")
    (tmp_path / "files" / "tool" / "1.0").mkdir(parents=True)
    (tmp_path / "files" / "tool" / "1.0" / "tool").write_bytes(b"x")
    assert files.fetch([need], tmp_path, Mirrors(), log=lambda *_: None) == {"downloaded": 0, "skipped": 1}
