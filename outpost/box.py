"""The box: the folder of everything fetch downloaded, with a manifest listing every file and its sha256.

    box/
      manifest.json
      manifest.sig       optional ed25519 signature made by `outpost pack`
      images/            one OCI layout folder per image
      pypi/              wheels and sdists
      npm/               tarballs
      apt/<distro>/      .deb files
      apk/<branch>/      .apk files (Alpine, branch v3.20)
      rpm/<distro>/      .rpm files (rocky9, alma9, fedora40, stream9)
      files/<name>/<version>/<filename>

`outpost pack` turns the folder into one tar.xz per kind so each file stays under GitHub's 2 GB
release limit. `outpost upload` accepts the folder or the tar files.

    write_manifest(box)                        # after fetch
    pack(box, Path("dist"), signer=...)        # one tar.xz per kind, plus the signed manifest
    box, tmp = open_box([Path("dist/box-images-2026-01-01.tar.xz"), ...])
    check_signature(box, workspace.box_public_key)   # SIGNATURE_VALID, UNSIGNED, ...
    verify_manifest(box)                       # [] when every file is there and intact
"""

import json
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, pki
from .http import sha256_file

KINDS = ("images", "pypi", "npm", "apt", "apk", "rpm", "files")
MANIFEST = "manifest.json"
SIGNATURE = "manifest.sig"

# what check_signature() found
SIGNATURE_VALID = "valid"
SIGNATURE_INVALID = "invalid"
UNSIGNED = "unsigned"  # the box has no manifest.sig
NO_PUBLIC_KEY = "no public key"  # the box is signed, but there is no pki/box.pub to check it with


def write_manifest(box: Path) -> dict:
    """List every file of every kind with its size and sha256 in manifest.json, and return the manifest.

    A manifest.sig left from an earlier `outpost pack` signed the old manifest, so it is removed.
    """
    items = []
    for kind in KINDS:
        root = box / kind
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            items.append({"path": str(path.relative_to(box)), "size": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "outpost": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": len(items),
        "bytes": sum(i["size"] for i in items),
        "items": items,
    }
    (box / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n")
    (box / SIGNATURE).unlink(missing_ok=True)
    return manifest


def verify_manifest(box: Path) -> list[str]:
    """The problems with the box, one line each. Empty when every listed file is present and intact."""
    manifest_file = box / MANIFEST
    if not manifest_file.exists():
        return ["manifest.json is missing (run `outpost fetch` or `outpost pack`)"]
    problems = []
    for item in json.loads(manifest_file.read_text())["items"]:
        path = box / item["path"]
        if not path.exists():
            problems.append(f"missing: {item['path']}")
        elif sha256_file(path) != item["sha256"]:
            problems.append(f"corrupt: {item['path']}")
    return problems


def check_signature(box: Path, box_public_key: Path) -> str:
    """Check manifest.sig against the box's public key (pki/box.pub).

    Returns:
        SIGNATURE_VALID or SIGNATURE_INVALID; UNSIGNED when the box has no manifest.sig, and
        NO_PUBLIC_KEY when there is no public key to check it with.
    """
    signature = box / SIGNATURE
    if not signature.exists():
        return UNSIGNED
    if not box_public_key.exists():
        return NO_PUBLIC_KEY
    if pki.signature_is_valid(box / MANIFEST, signature, box_public_key):
        return SIGNATURE_VALID
    return SIGNATURE_INVALID


def pack(box: Path, out_dir: Path, signer: Callable[[Path], Path] | None = None) -> list[Path]:
    """Write the manifest, then one tar.xz per kind and one with the manifest (and its signature).

    Args:
        signer: signs the manifest file and returns the signature's path (pki.sign_file with the box key).

    Returns:
        The tar.xz files, named box-<kind>-<date>.tar.xz and box-manifest-<date>.tar.xz.
    """
    manifest = write_manifest(box)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = manifest["created"][:10]
    made = []
    for kind in KINDS:
        if not (box / kind).exists() or not any((box / kind).rglob("*")):
            continue
        target = out_dir / f"box-{kind}-{stamp}.tar.xz"
        with tarfile.open(target, "w:xz") as tar:
            tar.add(box / kind, arcname=kind)
        made.append(target)
    meta = out_dir / f"box-manifest-{stamp}.tar.xz"
    with tarfile.open(meta, "w:xz") as tar:
        tar.add(box / MANIFEST, arcname=MANIFEST)
        if signer:
            tar.add(signer(box / MANIFEST), arcname=SIGNATURE)
    made.append(meta)
    return made


def unpack(archives: list[Path], dest: Path) -> Path:
    """Extract pack()'s tar files into one box folder.

    Raises:
        ValueError: an archive has a path that would land outside dest.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for archive in archives:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                if not (dest / member.name).resolve().is_relative_to(root):
                    raise ValueError(f"{archive.name}: refusing path {member.name}")
            tar.extractall(dest, filter="data")
    return dest


def open_box(source: Path | list[Path]) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """A box folder as given, or tar files unpacked into a temporary folder the caller must keep (and clean up)."""
    if isinstance(source, Path) and source.is_dir():
        return source, None
    archives = [source] if isinstance(source, Path) else list(source)
    tmp = tempfile.TemporaryDirectory(prefix="outpost-box-")
    return unpack(archives, Path(tmp.name)), tmp


def distros_in(box: Path, kind: str = "apt") -> list[str]:
    """The distro (apt, rpm) or branch (apk) folders in the box for one OS package kind."""
    root = box / kind
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
