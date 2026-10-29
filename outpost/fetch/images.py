"""Container images, pulled straight from a registry into OCI layout folders: box/images/<safe name>/."""

from pathlib import Path

from outpost import oci
from outpost.config import Mirrors
from outpost.http import Http


def fetch(refs: list[str], box: Path, mirrors: Mirrors, arch: str, log=print) -> dict:
    """Pull each image for linux/arch, the box's CPU type. Images already in the box are skipped.

    One image that fails does not stop the others.

    Returns:
        {"pulled": n, "skipped": n, "failed": ["<image>: <why>", ...]}.
    """
    done, skipped, failed = 0, 0, []
    for raw in refs:
        try:
            ref = oci.parse_ref(raw)
            dest = box / "images" / ref.safe_name
            if (dest / "index.json").exists():
                skipped += 1
                continue
            log(f"    {ref.short}  <- {registry_url(ref, mirrors)}")
            oci.Registry(Http(registry_url(ref, mirrors))).pull(ref, dest, arch=arch)
            done += 1
        except Exception as e:  # keep going; the summary lists it
            failed.append(f"{raw}: {e}")
            log(f"    FAILED {raw}: {e}")
    log(f"    {done} pulled, {skipped} already in box" + (f", {len(failed)} failed" if failed else ""))
    return {"pulled": done, "skipped": skipped, "failed": failed}


def registry_url(ref: oci.Ref, mirrors: Mirrors) -> str:
    """Where to pull ref from: the Docker Hub mirror from mirrors.yml, or the image's own registry."""
    return mirrors.docker if ref.registry == "docker.io" else f"https://{ref.registry}"
