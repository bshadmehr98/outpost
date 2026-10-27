"""Push a box into Gitea. Every file kind maps to one Gitea package registry: generic, PyPI, npm,
Debian (one suite per apt distro), Alpine (one branch per apk branch), RPM (one group per rpm distro)
and the container registry.

Everything lands in the one organisation, images included: `python:3.12-slim` becomes
`<gitea>/pkgs/library/python:3.12-slim` (the original path below the organisation) and Dockerfiles
reference that name explicitly.
Already-present files are skipped (Gitea answers 409), so re-uploading a box is cheap.
At the end the `config` image is built from the same client files every node gets,
so a Dockerfile only needs:  COPY --from=gitea.internal/pkgs/config / /
"""

import tempfile
from pathlib import Path

from .. import box as boxmod
from .. import oci
from ..infra import clients
from ..infra.clients import GiteaInfo, ProxyInfo
from .client import GiteaClient


def upload_box(box: Path, client: GiteaClient, gitea: GiteaInfo, ca_pem: str, log=print, proxy: ProxyInfo | None = None) -> dict:
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

    apk = box / "apk"
    if apk.exists():
        log("==> apk")
        for branch_dir in sorted(p for p in apk.iterdir() if p.is_dir()):
            for path in sorted(branch_dir.glob("*.apk")):
                tally("apk", client.upload_alpine(path, branch_dir.name))

    rpm = box / "rpm"
    if rpm.exists():
        log("==> rpm")
        for distro_dir in sorted(p for p in rpm.iterdir() if p.is_dir()):
            for path in sorted(distro_dir.glob("*.rpm")):
                tally("rpm", client.upload_rpm(path, group=distro_dir.name))

    images = box / "images"
    if images.exists():
        log("==> images")
        client.ensure_org(gitea.org)
        for layout in sorted(p for p in images.iterdir() if (p / "index.json").exists()):
            ref = oci.parse_ref(oci.layout_ref(layout))
            if not oci.GITEA_IMAGE_NAME.match(ref.mirror_name):
                raise ValueError(f"{ref.short}: Gitea does not accept {ref.mirror_name!r} as an image name")
            client.push_image(layout, gitea.org, ref.mirror_name, ref.tag)
            log(f"    {ref.short}  ->  {oci.gitea_ref(gitea, ref)}")
            tally("images", "uploaded")

    stats["config_image"] = push_config_image(client, gitea, ca_pem, boxmod.distros_in(box),
                                              apk_branches=boxmod.distros_in(box, "apk"), rpm_distros=boxmod.distros_in(box, "rpm"), log=log,
                                              proxy=proxy)

    for kind, s in stats.items():
        if "uploaded" in s:
            log(f"    {kind}: {s['uploaded']} uploaded, {s['exists']} already there")
    return stats


def push_config_image(client: GiteaClient, gitea: GiteaInfo, ca_pem: str, distros: list[str],
                      apk_branches: list[str] | None = None, rpm_distros: list[str] | None = None, log=print,
                      proxy: ProxyInfo | None = None) -> dict:
    """Build the one-layer `config` image from the client files and push it. Also run on its own
    after the CA is rotated, so builds pick up the new certificate without a full upload.
    `distros` are the apt suites, `apk_branches` the Alpine branches and `rpm_distros` the RPM groups
    that exist in Gitea; a package source is only written for what exists, and the keys with them.
    With an egress `proxy`, the image also carries /etc/outpost/proxy.env and the profile.d exports, for
    interactive shells and `env_file:`; a RUN step gets the variables from the node's Docker client config."""
    log("==> config image")
    apk_branches, rpm_distros = list(apk_branches or []), list(rpm_distros or [])
    apt_key = client.debian_key() if distros else None
    apk_key = client.alpine_key() if apk_branches else None
    rpm_key = client.rpm_key() if rpm_distros else None
    files = clients.config_image_files(gitea, ca_pem, apt_key, distros, apk_key=apk_key, apk_branches=apk_branches,
                                       rpm_key=rpm_key, rpm_distros=rpm_distros, proxy=proxy)
    with tempfile.TemporaryDirectory(prefix="outpost-config-image-") as tmp:
        layout = oci.build_layout(files, Path(tmp), gitea.config_image)
        client.ensure_org(gitea.org)
        client.push_image(layout, gitea.org, "config", "latest")
    log(f"    {gitea.config_image}  (COPY --from=... / / in your Dockerfiles)" + (f", with the egress proxy {proxy.url}" if proxy else ""))
    return {"distros": distros, "apk_branches": apk_branches, "rpm_distros": rpm_distros}
