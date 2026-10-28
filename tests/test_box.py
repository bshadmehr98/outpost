import shutil
import subprocess

import pytest

from outpost import box as boxmod
from outpost import pki

HAVE_OPENSSL = shutil.which("openssl") is not None


def make_box(root):
    (root / "pypi").mkdir(parents=True)
    (root / "pypi" / "a-1.0-py3-none-any.whl").write_bytes(b"a")
    (root / "apt" / "bookworm").mkdir(parents=True)
    (root / "apt" / "bookworm" / "curl.deb").write_bytes(b"deb")
    (root / "files" / "gitea" / "1.0").mkdir(parents=True)
    (root / "files" / "gitea" / "1.0" / "gitea").write_bytes(b"bin")
    return root


def test_manifest_write_and_verify(tmp_path):
    box = make_box(tmp_path / "box")
    manifest = boxmod.write_manifest(box)
    assert manifest["files"] == 3 and manifest["bytes"] == 7
    assert [i["path"] for i in manifest["items"]] == [
        "pypi/a-1.0-py3-none-any.whl",
        "apt/bookworm/curl.deb",
        "files/gitea/1.0/gitea",
    ]
    assert boxmod.verify_manifest(box) == []
    (box / "pypi" / "a-1.0-py3-none-any.whl").write_bytes(b"changed")
    (box / "apt" / "bookworm" / "curl.deb").unlink()
    assert boxmod.verify_manifest(box) == ["corrupt: pypi/a-1.0-py3-none-any.whl", "missing: apt/bookworm/curl.deb"]
    assert boxmod.distros_in(box) == ["bookworm"]
    assert "missing" in boxmod.verify_manifest(tmp_path / "empty")[0]


def test_pack_unpack_roundtrip(tmp_path):
    box = make_box(tmp_path / "box")
    made = boxmod.pack(box, tmp_path / "dist")
    names = sorted(p.name.split("-")[1] for p in made)
    assert names == ["apt", "files", "manifest", "pypi"]
    opened, keep = boxmod.open_box(made)
    assert keep is not None
    assert boxmod.verify_manifest(opened) == []
    keep.cleanup()
    same, keep = boxmod.open_box(box)
    assert same == box and keep is None


def test_unpack_rejects_escaping_paths(tmp_path):
    import tarfile

    evil = tmp_path / "evil.tar.xz"
    with tarfile.open(evil, "w:xz") as tar:
        tar.add(__file__, arcname="../../escape.py")
    with pytest.raises(ValueError, match="refusing path"):
        boxmod.unpack([evil], tmp_path / "out")


@pytest.mark.skipif(not HAVE_OPENSSL, reason="openssl not installed")
def test_signing(tmp_path):
    pub, key = pki.ensure_signing_key(tmp_path / "pki", tmp_path / "secrets")
    assert oct(key.stat().st_mode)[-3:] == "600"
    box = make_box(tmp_path / "box")
    made = boxmod.pack(box, tmp_path / "dist", signer=lambda m: pki.sign(m, key))
    opened, keep = boxmod.open_box(made)
    assert pki.verify(opened / "manifest.json", opened / "manifest.sig", pub)
    (opened / "manifest.json").write_text("tampered")
    assert not pki.verify(opened / "manifest.json", opened / "manifest.sig", pub)
    keep.cleanup()


@pytest.mark.skipif(not HAVE_OPENSSL, reason="openssl not installed")
def test_ca_and_server_cert(tmp_path):
    ca = pki.ensure_ca(tmp_path / "pki", tmp_path / "secrets")
    cert, key = pki.ensure_server_cert(tmp_path / "pki", tmp_path / "secrets", "gitea.internal", "10.0.0.5")
    text = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-text"], capture_output=True, text=True
    ).stdout
    assert "DNS:gitea.internal" in text and "IP Address:10.0.0.5" in text
    assert subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(cert)], capture_output=True).returncode == 0
    assert pki.ensure_server_cert(tmp_path / "pki", tmp_path / "secrets", "gitea.internal", "10.0.0.5") == (cert, key)
