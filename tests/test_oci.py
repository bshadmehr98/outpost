import json

import pytest

from outpost import oci
from outpost.http import Http


@pytest.mark.parametrize("ref,expected", [
    ("python:3.12-slim", ("docker.io", "library/python", "3.12-slim")),
    ("python", ("docker.io", "library/python", "latest")),
    ("grafana/grafana:10.2", ("docker.io", "grafana/grafana", "10.2")),
    ("ghcr.io/org/app:v1", ("ghcr.io", "org/app", "v1")),
    ("localhost:5000/x/y:1", ("localhost:5000", "x/y", "1")),
])
def test_parse_ref(ref, expected):
    r = oci.parse_ref(ref)
    assert (r.registry, r.repo, r.tag) == expected
    assert oci.parse_ref(r.short) == r


def test_ref_names():
    r = oci.parse_ref("python:3.12-slim")
    assert (r.owner, r.image, r.short, r.safe_name) == ("library", "python", "python:3.12-slim", "python_3.12-slim")
    assert oci.parse_ref("ghcr.io/org/app:v1").short == "ghcr.io/org/app:v1"
    with pytest.raises(ValueError):
        oci.parse_ref("python@sha256:abc")


def test_build_layout_is_consistent(tmp_path):
    layout = oci.build_layout({"/etc/pip.conf": (b"[global]\n", 0o644)}, tmp_path / "img", "x/config:latest")
    index = json.loads((layout / "index.json").read_text())
    entry = index["manifests"][0]
    manifest_bytes = (layout / "blobs/sha256" / entry["digest"][7:]).read_bytes()
    assert oci.digest_of(manifest_bytes) == entry["digest"]
    manifest = json.loads(manifest_bytes)
    for blob in [manifest["config"]] + manifest["layers"]:
        data = (layout / "blobs/sha256" / blob["digest"][7:]).read_bytes()
        assert oci.digest_of(data) == blob["digest"] and len(data) == blob["size"]
    config = json.loads((layout / "blobs/sha256" / manifest["config"]["digest"][7:]).read_bytes())
    assert config["rootfs"]["diff_ids"][0].startswith("sha256:")
    assert oci.layout_ref(layout) == "x/config:latest"


def test_pull_with_token_and_multiarch_index(tmp_path, fake_gitea):
    source = oci.build_layout({"/hello": (b"hi", 0o644)}, tmp_path / "src", "library/alpine:3.20")
    fake_gitea.add_layout(source, "library/alpine", "3.20", add_index=True)

    registry = oci.Registry(Http(fake_gitea.url))
    pulled = registry.pull(oci.parse_ref("alpine:3.20"), tmp_path / "pulled")
    assert oci.layout_ref(pulled) == "alpine:3.20"
    src_blobs = sorted(p.name for p in (source / "blobs/sha256").iterdir())
    assert sorted(p.name for p in (pulled / "blobs/sha256").iterdir()) == src_blobs
    assert any(path.startswith("/token") for _, path in fake_gitea.requests), "token challenge was answered"


def test_push_then_pull_roundtrip(tmp_path, fake_gitea):
    source = oci.build_layout({"/etc/x": (b"y", 0o644)}, tmp_path / "src", "pkgs/config:latest")
    registry = oci.Registry(Http(fake_gitea.url), token_url=f"{fake_gitea.url}/v2/token")
    digest = registry.push(source, "pkgs/config", "latest")
    assert ("pkgs/config", "latest") in fake_gitea.manifests
    assert fake_gitea.manifests[("pkgs/config", "latest")][1] == oci.MANIFEST_TYPES[0]
    # second push uploads no blobs again
    before = len([r for r in fake_gitea.requests if r[0] == "POST"])
    registry.push(source, "pkgs/config", "latest")
    assert len([r for r in fake_gitea.requests if r[0] == "POST"]) == before
    pulled = oci.Registry(Http(fake_gitea.url)).pull(oci.parse_ref("example.com/pkgs/config:latest"), tmp_path / "back")
    assert json.loads((pulled / "index.json").read_text())["manifests"][0]["digest"] == digest
