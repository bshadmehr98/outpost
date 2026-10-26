"""Push a box into Gitea. Every file kind maps to one Gitea package registry.

Already-present files are skipped (Gitea answers 409), so re-uploading a box is cheap.
At the end the `config` image is built from the same client files every node gets,
so a Dockerfile only needs:  COPY --from=gitea.internal/pkgs/config / /
"""

import tempfile
from pathlib import Path

from .. import box as boxmod
from .. import oci
from ..infra import clients
from ..infra.clients import GiteaInfo
from .client import GiteaClient


def upload_box(box: Path, client: GiteaClient, gitea: GiteaInfo, ca_pem: str, log=print) -> dict:
    problems = boxmod.verify_manifest(box)
    if problems:
        raise RuntimeError("The box does not match its manifest:\n  " + "\n  ".join(problems[:10]))
    stats: dict[str, dict] = {}

    def tally(kind: str, outcome: str) -> None:
        stats.setdefault(kind, {"uploaded": 0, "exists": 0})[outcome] += 1

    files = box / "files"
    if files.exists():
        log("==> files")
        for path in sorted(p for p in files.rglob("*") if p.is_file()):
            name, version = path.relative_to(files).parts[:2]
            tally("files", client.upload_generic(name, version, path))

    pypi = box / "pypi"
    if pypi.exists():
        log("==> pypi")
        for path in sorted(p for p in pypi.iterdir() if p.suffix in (".whl", ".gz", ".zip")):
            tally("pypi", client.upload_pypi(path))

    npm = box / "npm"
    if npm.exists():
        log("==> npm")
        for path in sorted(npm.glob("*.tgz")):
            tally("npm", client.upload_npm(path))

    apt = box / "apt"
    if apt.exists():
        log("==> apt")
        for distro_dir in sorted(p for p in apt.iterdir() if p.is_dir()):
            for path in sorted(distro_dir.glob("*.deb")):
                tally("apt", client.upload_debian(path, distro_dir.name))

    images = box / "images"
    if images.exists():
        log("==> images")
        for layout in sorted(p for p in images.iterdir() if (p / "index.json").exists()):
            ref = oci.parse_ref(oci.layout_ref(layout))
            client.ensure_org(ref.owner)
            client.push_image(layout, ref.owner, ref.image, ref.tag)
            log(f"    {ref.short}  ->  {gitea.name}/{ref.repo}:{ref.tag}")
            tally("images", "uploaded")

    stats["config_image"] = push_config_image(client, gitea, ca_pem, boxmod.distros_in(box), log=log)

    for kind, s in stats.items():
        if "uploaded" in s:
            log(f"    {kind}: {s['uploaded']} uploaded, {s['exists']} already there")
    return stats


def push_config_image(client: GiteaClient, gitea: GiteaInfo, ca_pem: str, distros: list[str], log=print) -> dict:
    """Build the one-layer `config` image from the client files and push it. Also run on its own
    after the CA is rotated, so builds pick up the new certificate without a full upload."""
    log("==> config image")
    apt_key = client.debian_key() if distros else None
    with tempfile.TemporaryDirectory(prefix="outpost-config-image-") as tmp:
        layout = oci.build_layout(clients.config_image_files(gitea, ca_pem, apt_key, distros), Path(tmp), gitea.config_image)
        client.ensure_org(gitea.org)
        client.push_image(layout, gitea.org, "config", "latest")
    log(f"    {gitea.config_image}  (COPY --from=... / / in your Dockerfiles)")
    return {"distros": distros}
