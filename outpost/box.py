"""The box: a folder of downloaded artifacts with a manifest listing every file and its sha256.

    box/
      manifest.json
      manifest.sig       optional ed25519 signature made by `outpost pack`
      images/            one OCI layout folder per image
      pypi/              wheels and sdists
      npm/               tarballs
      apt/<distro>/      .deb files
      files/<name>/<version>/<filename>

`outpost pack` turns the folder into one tar.xz per kind so each file stays under
GitHub's 2 GB release limit. `outpost upload` accepts the folder or the tar files.
"""

import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .http import sha256_file

KINDS = ("images", "pypi", "npm", "apt", "files")


def write_manifest(box: Path) -> dict:
    items = []
    for kind in KINDS:
        root = box / kind
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            items.append({"path": str(path.relative_to(box)), "size": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "outpost": __version__,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len(items),
        "bytes": sum(i["size"] for i in items),
        "items": items,
    }
    (box / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def verify_manifest(box: Path) -> list[str]:
    """Return a list of problems. Empty list means every listed file is present and intact."""
    manifest_file = box / "manifest.json"
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


def pack(box: Path, out_dir: Path, signer=None) -> list[Path]:
    """One tar.xz per kind plus the manifest. `signer(manifest_path) -> sig_path` is optional."""
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
        tar.add(box / "manifest.json", arcname="manifest.json")
        if signer:
            sig = signer(box / "manifest.json")
            tar.add(sig, arcname="manifest.sig")
    made.append(meta)
    return made


def unpack(archives: list[Path], dest: Path) -> Path:
    """Extract pack() output into one box folder. Safe against paths that escape dest."""
    dest.mkdir(parents=True, exist_ok=True)
    for archive in archives:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError(f"{archive.name}: refusing path {member.name}")
            tar.extractall(dest, filter="data")
    return dest


def open_box(source: Path | list[Path]) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """A box folder as given, or tar files unpacked into a temp dir the caller must keep alive."""
    if isinstance(source, Path) and source.is_dir():
        return source, None
    archives = [source] if isinstance(source, Path) else list(source)
    tmp = tempfile.TemporaryDirectory(prefix="outpost-box-")
    return unpack(archives, Path(tmp.name)), tmp


def distros_in(box: Path) -> list[str]:
    apt = box / "apt"
    return sorted(p.name for p in apt.iterdir() if p.is_dir()) if apt.exists() else []
