"""Push a box into Gitea. Every kind goes into one Gitea package registry: generic, PyPI, npm,
Debian (one suite per apt distro), Alpine (one branch per apk branch), RPM (one group per rpm distro)
and the container registry.

    upload(system, sources=[], log=print)      # the workspace's box/, or folders and tar.xz files

Everything lands in the one organisation, images included: `python:3.12-slim` becomes
`<gitea>/pkgs/library/python:3.12-slim` (the original path below the organisation) and Dockerfiles
reference that name explicitly. Files Gitea already has are skipped (it answers 409), so uploading
a box again is cheap. At the end the `config` image is built from the same client files every node
gets, so a Dockerfile only needs:  COPY --from=gitea.internal/pkgs/config / /
"""

import tempfile
from collections.abc import Callable
from pathlib import Path

from outpost import box as boxmod
from outpost import (
    client_config,
    gitea,  # gitea.connect through the package, so tests replace one name: outpost.gitea.connect
    oci,
    pki,
)
from outpost.endpoints import GiteaEndpoint, ProxyEndpoint, gitea_endpoint, proxy_endpoint_or_none
from outpost.gitea.client import GiteaClient
from outpost.gitea.upload_formats import EXISTS, UPLOADED
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_UPLOAD


def upload(system: AirGappedSystem, sources: list[Path], log=print) -> dict:
    """Push a box into Gitea and record what is there now in state/upload.json.

    Args:
        sources: a box folder, or the tar.xz files `outpost pack` made; empty means the workspace's box/.

    Returns:
        The upload's numbers per kind, and what the config image was built for.

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
        endpoint = gitea_endpoint(system)
        proxy = proxy_endpoint_or_none(system, endpoint, log)
        with gitea.connect(system) as client:
            stats = upload_box(box, client, endpoint, system.ca_pem(), system.arch, log=log, proxy=proxy)
    finally:
        if unpacked:
            unpacked.cleanup()
    _record_upload(system, stats, sources)
    return stats


def upload_box(
    box: Path,
    client: GiteaClient,
    gitea: GiteaEndpoint,
    ca_pem: str,
    arch: str,
    log=print,
    proxy: ProxyEndpoint | None = None,
) -> dict:
    """Upload every kind in an open box folder, then build and push the config image for the CPU type arch.

    Returns:
        {kind: {"uploaded": n, "exists": m}} for each kind in the box, and "config_image".

    Raises:
        RuntimeError: the box does not match its manifest.
    """
    problems = boxmod.verify_manifest(box)
    if problems:
        raise RuntimeError("The box does not match its manifest:\n  " + "\n  ".join(problems[:10]))
    tally = Tally()
    for kind, upload_kind in KIND_UPLOADERS.items():
        if (box / kind).exists():
            log(f"==> {kind}")
            upload_kind(box / kind, client, gitea, tally, log)
    stats: dict[str, dict] = tally.counts()
    stats["config_image"] = push_config_image(
        client,
        gitea,
        ca_pem,
        arch,
        boxmod.distros_in(box),
        apk_branches=boxmod.distros_in(box, "apk"),
        rpm_distros=boxmod.distros_in(box, "rpm"),
        log=log,
        proxy=proxy,
    )
    for kind, counts in tally.counts().items():
        log(f"    {kind}: {counts[UPLOADED]} uploaded, {counts[EXISTS]} already there")
    return stats


def push_config_image(
    client: GiteaClient,
    gitea: GiteaEndpoint,
    ca_pem: str,
    arch: str,
    distros: list[str],
    apk_branches: list[str] | None = None,
    rpm_distros: list[str] | None = None,
    log=print,
    proxy: ProxyEndpoint | None = None,
) -> dict:
    """Build the one-layer `config` image from the client files and push it.

    Also run on its own after the CA is rotated, so builds pick up the new certificate without a
    full upload. A package source is only written for what exists in Gitea, and its key with it.
    With an egress proxy, the image also carries /etc/outpost/proxy.env and the profile.d exports,
    for interactive shells and `env_file:`; a RUN step gets the variables from the node's Docker
    client config.

    Args:
        arch: the box's CPU type; the image is built for it.
        distros: the apt suites in Gitea.
        apk_branches: the Alpine branches in Gitea.
        rpm_distros: the RPM groups in Gitea.
        proxy: the egress proxy, when there is one.

    Returns:
        What the image was built for: {"distros", "apk_branches", "rpm_distros"}.
    """
    log("==> config image")
    apk_branches, rpm_distros = list(apk_branches or []), list(rpm_distros or [])
    packages = client.packages
    files = client_config.config_image_files(
        gitea,
        ca_pem,
        packages.debian_key() if distros else None,
        distros,
        apk_key=packages.alpine_key() if apk_branches else None,
        apk_branches=apk_branches,
        rpm_key=packages.rpm_key() if rpm_distros else None,
        rpm_distros=rpm_distros,
        proxy=proxy,
        ca_hash=pki.subject_hash(ca_pem),
    )
    with tempfile.TemporaryDirectory(prefix="outpost-config-image-") as tmp:
        layout = oci.build_layout(files, Path(tmp), gitea.config_image, arch)
        client.ensure_org(gitea.org)
        packages.push_image(layout, gitea.org, "config", "latest")
    log(
        f"    {gitea.config_image}  (COPY --from=... / / in your Dockerfiles)"
        + (f", with the egress proxy {proxy.url}" if proxy else "")
    )
    return {"distros": distros, "apk_branches": apk_branches, "rpm_distros": rpm_distros}


class Tally:
    """How many files of each kind were uploaded, and how many Gitea already had."""

    def __init__(self):
        self._counts: dict[str, dict[str, int]] = {}

    def add(self, kind: str, outcome: str) -> None:
        """Count one file; outcome is "uploaded" or "exists"."""
        self._counts.setdefault(kind, {UPLOADED: 0, EXISTS: 0})[outcome] += 1

    def counts(self) -> dict[str, dict[str, int]]:
        """{kind: {"uploaded": n, "exists": m}}, for the kinds that had a file."""
        return {kind: dict(counts) for kind, counts in self._counts.items()}


# -- one uploader per kind: (the kind's folder in the box, client, gitea, tally, log) --------------


def _upload_files(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """files/<name>/<version>/<file> into the generic registry."""
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        name, version = path.relative_to(folder).parts[:2]
        tally.add("files", client.packages.upload_generic(name, version, path))


def _upload_pypi(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """Wheels and sdists into the PyPI registry (not pip's .requirements-*.txt)."""
    for path in sorted(p for p in folder.iterdir() if p.suffix in (".whl", ".gz", ".zip")):
        tally.add("pypi", client.packages.upload_pypi(path))


def _upload_npm(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """npm tarballs into the npm registry."""
    for path in sorted(folder.glob("*.tgz")):
        tally.add("npm", client.packages.upload_npm(path))


def _upload_apt(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """apt/<distro>/*.deb into the Debian registry, one suite per distro."""
    for distro, path in _files_per_subfolder(folder, "*.deb"):
        tally.add("apt", client.packages.upload_debian(path, distro))


def _upload_apk(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """apk/<branch>/*.apk into the Alpine registry, one branch per Alpine branch."""
    for branch, path in _files_per_subfolder(folder, "*.apk"):
        tally.add("apk", client.packages.upload_alpine(path, branch))


def _upload_rpm(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """rpm/<distro>/*.rpm into the RPM registry, one group per distro."""
    for distro, path in _files_per_subfolder(folder, "*.rpm"):
        tally.add("rpm", client.packages.upload_rpm(path, group=distro))


def _upload_images(folder: Path, client: GiteaClient, gitea: GiteaEndpoint, tally: Tally, log) -> None:
    """Every OCI layout into the container registry, below the organisation.

    Raises:
        ValueError: Gitea would refuse an image's name.
    """
    client.ensure_org(gitea.org)
    for layout in sorted(p for p in folder.iterdir() if (p / "index.json").exists()):
        ref = oci.parse_ref(oci.layout_ref(layout))
        if not oci.GITEA_IMAGE_NAME.match(ref.mirror_name):
            raise ValueError(f"{ref.short}: Gitea does not accept {ref.mirror_name!r} as an image name")
        pushed = client.packages.push_image(layout, gitea.org, ref.mirror_name, ref.tag)
        log(f"    {ref.short}  ->  {oci.gitea_ref(gitea, ref)}")
        tally.add("images", UPLOADED if pushed.uploaded else EXISTS)


Uploader = Callable[[Path, GiteaClient, GiteaEndpoint, Tally, Callable], None]

KIND_UPLOADERS: dict[str, Uploader] = {  # in the order they are uploaded; the key is the box folder
    "files": _upload_files,
    "pypi": _upload_pypi,
    "npm": _upload_npm,
    "apt": _upload_apt,
    "apk": _upload_apk,
    "rpm": _upload_rpm,
    "images": _upload_images,
}


def _files_per_subfolder(folder: Path, pattern: str):
    """(subfolder name, file) for each file matching pattern, one subfolder (a distro or branch) at a time."""
    for subfolder in sorted(p for p in folder.iterdir() if p.is_dir()):
        for path in sorted(subfolder.glob(pattern)):
            yield subfolder.name, path


# -- before and after -------------------------------------------------------------------------


def _check_signature(box: Path, box_public_key: Path, log) -> None:
    """Check manifest.sig against pki/box.pub; say so when there is nothing to check it with.

    Raises:
        RuntimeError: the signature does not match.
    """
    found = boxmod.check_signature(box, box_public_key)
    if found == boxmod.UNSIGNED:
        log("warning: the box is not signed (no manifest.sig), so its signature is not checked")
    elif found == boxmod.NO_PUBLIC_KEY:
        log("warning: cannot check the box's signature: pki/box.pub is missing")
    elif found == boxmod.SIGNATURE_INVALID:
        raise RuntimeError("manifest.sig does not match pki/box.pub. The box was altered or signed with another key.")
    else:
        log("Signature OK")


def _record_upload(system: AirGappedSystem, stats: dict, sources: list[Path]) -> None:
    """state/upload.json: what the config image was built for, the numbers, and where the box came from."""
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
