"""The OCI layout on disk, and a small image built from files with no Docker.

box/images/python_3.12-slim/
  oci-layout
  index.json                 points at the manifest, remembers the original name
  blobs/sha256/<digest>      manifest, config and compressed layers, verbatim
"""

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from outpost.oci.media_types import OCI_CONFIG, OCI_INDEX, OCI_LAYER_GZIP, OCI_MANIFEST, REF_ANNOTATION

LAYOUT_VERSION = '{"imageLayoutVersion": "1.0.0"}\n'


def digest_of(data: bytes) -> str:
    """sha256:<hex> of data, the way registries name blobs."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def blobs_dir(layout: Path) -> Path:
    """Where a layout keeps its blobs, by digest."""
    return layout / "blobs" / "sha256"


def blob_path(layout: Path, digest: str) -> Path:
    """The file of one blob: sha256:abc... -> blobs/sha256/abc..."""
    return blobs_dir(layout) / digest.removeprefix("sha256:")


def write_index(layout: Path, manifest: bytes, media_type: str, ref_name: str) -> None:
    """Write oci-layout and index.json, pointing at one manifest and naming the image ref_name."""
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [
            {
                "mediaType": media_type,
                "digest": digest_of(manifest),
                "size": len(manifest),
                "annotations": {REF_ANNOTATION: ref_name},
            }
        ],
    }
    (layout / "oci-layout").write_text(LAYOUT_VERSION)
    (layout / "index.json").write_text(json.dumps(index, indent=2) + "\n")


def layout_ref(layout: Path) -> str:
    """The original reference an OCI layout was pulled as."""
    index = json.loads((layout / "index.json").read_text())
    return index["manifests"][0].get("annotations", {}).get(REF_ANNOTATION, layout.name)


def build_layout(files: dict[str, tuple[bytes, int]], dest: Path, ref_name: str, arch: str) -> Path:
    """Create a one-layer image from {path: (content, mode)}, for the CPU type arch. Used for the config image."""
    layer_tar = _tar_of(files)
    layer_gz = gzip.compress(layer_tar, mtime=0)
    config = json.dumps(_image_config(digest_of(layer_tar), arch), sort_keys=True).encode()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": {"mediaType": OCI_CONFIG, "digest": digest_of(config), "size": len(config)},
            "layers": [{"mediaType": OCI_LAYER_GZIP, "digest": digest_of(layer_gz), "size": len(layer_gz)}],
        },
        sort_keys=True,
    ).encode()
    blobs_dir(dest).mkdir(parents=True, exist_ok=True)
    for data in (layer_gz, config, manifest):
        blob_path(dest, digest_of(data)).write_bytes(data)
    write_index(dest, manifest, OCI_MANIFEST, ref_name)
    return dest


def _tar_of(files: dict[str, tuple[bytes, int]]) -> bytes:
    """A tar of the files, sorted and dated 1970, so the same files always give the same digest."""
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as tar:
        for path in sorted(files):
            content, mode = files[path]
            info = tarfile.TarInfo(name=path.lstrip("/"))
            info.size = len(content)
            info.mode = mode
            info.mtime = 0
            tar.addfile(info, io.BytesIO(content))
    return layer.getvalue()


def _image_config(layer_diff_id: str, arch: str) -> dict:
    return {
        "architecture": arch,
        "os": "linux",
        "config": {"Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]},
        "rootfs": {"type": "layers", "diff_ids": [layer_diff_id]},
        "history": [{"created_by": "outpost config image"}],
    }
