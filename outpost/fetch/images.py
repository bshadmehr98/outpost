"""Container images, pulled straight from a registry into OCI layout folders."""

from pathlib import Path

from .. import oci, versions
from ..http import Http


def registry_url(ref: oci.Ref, mirrors) -> str:
    return mirrors.docker if ref.registry == "docker.io" else f"https://{ref.registry}"


def fetch(refs: list[str], box: Path, mirrors, log=print, arch: str | None = None) -> dict:
    arch = arch or versions.ARCH
    done, skipped = 0, 0
    for raw in refs:
        ref = oci.parse_ref(raw)
        dest = box / "images" / ref.safe_name
        if (dest / "index.json").exists():
            skipped += 1
            continue
        log(f"    {ref.short}  <- {registry_url(ref, mirrors)}")
        oci.Registry(Http(registry_url(ref, mirrors))).pull(ref, dest, arch=arch)
        done += 1
    log(f"    {done} pulled, {skipped} already in box")
    return {"pulled": done, "skipped": skipped}
