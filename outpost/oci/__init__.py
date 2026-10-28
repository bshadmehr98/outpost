"""Pull and push container images with nothing but HTTP. A tiny, readable skopeo.

    ref = parse_ref("python:3.12-slim")                      # docker.io/library/python:3.12-slim
    Registry(Http("https://registry-1.docker.io")).pull(ref, Path("box/images") / ref.safe_name)
    Registry(gitea_http, token_url=...).push(layout, "pkgs/library/python", "3.12-slim")
    build_layout({"/etc/pip.conf": (b"...", 0o644)}, Path("config"), "pkgs/config:latest")

Images are stored on disk in the standard OCI layout, which Docker and skopeo also understand
(see layout.py).

    refs.py          Ref, parse_ref(), gitea_ref(), original_ref(), GITEA_IMAGE_NAME
    registry.py      Registry: pull, push (says in Pushed whether anything was new), tokens
    layout.py        the OCI layout: build_layout(), layout_ref(), write_index(), digest_of()
    media_types.py   manifest and index media types, REF_ANNOTATION

Code outside this folder uses only the names below.
"""

from outpost.oci.layout import build_layout, digest_of, layout_ref, write_index
from outpost.oci.media_types import ACCEPT, INDEX_TYPES, MANIFEST_TYPES, REF_ANNOTATION
from outpost.oci.refs import GITEA_IMAGE_NAME, Ref, gitea_ref, original_ref, parse_ref
from outpost.oci.registry import Pushed, Registry

__all__ = [
    "ACCEPT",
    "GITEA_IMAGE_NAME",
    "INDEX_TYPES",
    "MANIFEST_TYPES",
    "REF_ANNOTATION",
    "Pushed",
    "Ref",
    "Registry",
    "build_layout",
    "digest_of",
    "gitea_ref",
    "layout_ref",
    "original_ref",
    "parse_ref",
    "write_index",
]
