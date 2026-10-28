"""Push a box into Gitea. Every file kind maps to one Gitea package registry: generic, PyPI, npm,
Debian (one suite per apt distro), Alpine (one branch per apk branch), RPM (one group per rpm distro)
and the container registry.

Everything lands in the one organisation, images included: `python:3.12-slim` becomes
`<gitea>/pkgs/library/python:3.12-slim` (the original path below the organisation) and Dockerfiles
reference that name explicitly.
Already-present files are skipped (Gitea answers 409), so re-uploading a box is cheap.
At the end the `config` image is built from the same client files every node gets,
so a Dockerfile only needs:  COPY --from=gitea.internal/pkgs/config / /

    upload(system, sources=[], log=print)      # the workspace's box/, or folders and tar.xz files
"""

import tempfile
from pathlib import Path

from .. import box as boxmod
from .. import oci, pki
from ..endpoints import GiteaEndpoint, ProxyEndpoint, gitea_endpoint, proxy_endpoint_or_none
from ..infra import clients
from ..system import AirGappedSystem
from ..workspace import STATE_UPLOAD
from .client import GiteaClient
from .connect import connect


def upload(system: AirGappedSystem, sources: list[Path], log=print) -> dict:
    """Push a box into Gitea and record what is there now in state/upload.json.

    Args:
        sources: a box folder, or the tar.xz files `outpost pack` made; empty means the workspace's box/.

    Returns:
        The upload's numbers per kind.

    Raises:
        RuntimeError: a source is missing, or the box's signature or manifest does not match.
    """
    sources = sources or [system.workspace.box_dir]
    for source in sources:
        if not source.exists():
            raise RuntimeError(
                f"{source} does not exist. Run `outpost fetch` first, or pass the box folder or tar.xz files."
            )
    box, unpacked = boxmod.open_box(sources[0] if len(sources) == 1 and sources[0].is_dir() else sources)
    try:
        _check_signature(box, system.workspace.box_public_key, log)
        gitea = gitea_endpoint(system)
        proxy = proxy_endpoint_or_none(system, gitea, log)
        with connect(system.workspace, system.servers) as client:
            stats = upload_box(box, client, gitea, system.ca_pem(), log=log, proxy=proxy)
    finally:
        if unpacked:
            unpacked.cleanup()
    config_image = stats.get("config_image", {})
    system.write_state(
        STATE_UPLOAD,
        {
            "distros": config_image.get("distros", []),
            "apk_branches": config_image.get("apk_branches", []),
            "rpm_distros": config_image.get("rpm_distros", []),
            "stats": stats,
            "source": [str(source) for source in sources],
        },
    )
    return stats


def _check_signature(box: Path, box_public_key: Path, log) -> None:
    """Check manifest.sig against pki/box.pub; say so when there is nothing to check it with."""
    signature = box / "manifest.sig"
    if not signature.exists():
        log("warning: the box is not signed (no manifest.sig), so its signature is not checked")
        return
    if not box_public_key.exists():
        log("warning: cannot check the box's signature: pki/box.pub is missing")
        return
    if not pki.verify(box / "manifest.json", signature, box_public_key):
        raise RuntimeError("manifest.sig does not match pki/box.pub. The box was altered or signed with another key.")
    log("Signature OK")


def upload_box(  # noqa: C901 too complex; split into named steps in the refactor
    box: Path, client: GiteaClient, gitea: GiteaEndpoint, ca_pem: str, log=print, proxy: ProxyEndpoint | None = None
) -> dict:
    """Upload every kind in an open box folder, then build and push the config image."""
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

    stats["config_image"] = push_config_image(
        client,
        gitea,
        ca_pem,
        boxmod.distros_in(box),
        apk_branches=boxmod.distros_in(box, "apk"),
        rpm_distros=boxmod.distros_in(box, "rpm"),
        log=log,
        proxy=proxy,
    )

    for kind, s in stats.items():
        if "uploaded" in s:
            log(f"    {kind}: {s['uploaded']} uploaded, {s['exists']} already there")
    return stats


def push_config_image(
    client: GiteaClient,
    gitea: GiteaEndpoint,
    ca_pem: str,
    distros: list[str],
    apk_branches: list[str] | None = None,
    rpm_distros: list[str] | None = None,
    log=print,
    proxy: ProxyEndpoint | None = None,
) -> dict:
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
    files = clients.config_image_files(
        gitea,
        ca_pem,
        apt_key,
        distros,
        apk_key=apk_key,
        apk_branches=apk_branches,
        rpm_key=rpm_key,
        rpm_distros=rpm_distros,
        proxy=proxy,
        ca_hash=pki.subject_hash(ca_pem),
    )
    with tempfile.TemporaryDirectory(prefix="outpost-config-image-") as tmp:
        layout = oci.build_layout(files, Path(tmp), gitea.config_image)
        client.ensure_org(gitea.org)
        client.push_image(layout, gitea.org, "config", "latest")
    log(
        f"    {gitea.config_image}  (COPY --from=... / / in your Dockerfiles)"
        + (f", with the egress proxy {proxy.url}" if proxy else "")
    )
    return {"distros": distros, "apk_branches": apk_branches, "rpm_distros": rpm_distros}
