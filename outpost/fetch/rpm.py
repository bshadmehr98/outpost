"""RPM packages for the RHEL family, fetched inside a container of the matching distro, like apt.py.

dnf resolves dependencies against the distro's own metadata. `dnf download --resolve --alldeps`
writes the whole dependency tree, including packages the container already has (glibc,
openssl-libs...), so the files install on a minimal image, a full image or a server alike.

A distro is named `<family><major>`: rocky9, alma9, fedora40, stream9 (CentOS Stream; see
distros.py). The stock repo files of the container are replaced by ones built from mirrors.yml, so
a blocked network only needs the mirror keys changed, as for apt.
"""

import shlex
import subprocess
from pathlib import Path

from outpost import versions
from outpost.config import Mirrors
from outpost.distros import RPM_IMAGES, split_rpm_distro
from outpost.fetch.in_container import OUT, OsPackageRecipe, fetch_os_packages

# family -> (the mirrors.yml key, the repositories under that mirror)
REPOSITORIES = {
    "rocky": ("rocky", ("{major}/BaseOS/{arch}/os", "{major}/AppStream/{arch}/os", "{major}/extras/{arch}/os")),
    "alma": ("alma", ("{major}/BaseOS/{arch}/os", "{major}/AppStream/{arch}/os", "{major}/extras/{arch}/os")),
    "fedora": ("fedora", ("releases/{major}/Everything/{arch}/os", "updates/{major}/Everything/{arch}")),
    "stream": (
        "centos_stream",
        (
            "{major}-stream/BaseOS/{arch}/os",
            "{major}-stream/AppStream/{arch}/os",
            "{major}-stream/extras-common/{arch}/os",
        ),
    ),
}


def fetch(rpm: dict[str, list[str]], box: Path, mirrors: Mirrors, log=print, run=subprocess.run) -> dict:
    """Fetch each distro's packages and their whole dependency tree into box/rpm/<distro>.

    Returns:
        {distro: number of .rpm files}.
    """
    recipe = OsPackageRecipe(
        image_for=image_for,
        script_for=lambda distro, packages: script_for(distro, packages, mirrors),
        shell="bash",
        file_suffix=".rpm",
    )
    return fetch_os_packages("rpm", rpm, box, recipe, log=log, run=run)


def image_for(distro: str) -> str:
    """rocky9 -> rockylinux:9, stream9 -> quay.io/centos/centos:stream9.

    Raises:
        ValueError: distro is not an rpm distro Outpost knows.
    """
    family, major = split_rpm_distro(distro)
    image = RPM_IMAGES[family]
    return f"{image}:stream{major}" if family == "stream" else f"{image}:{major}"


def script_for(distro: str, packages: list[str], mirrors: Mirrors) -> str:
    """The script that points dnf at the mirror and downloads the packages' whole tree into /out."""
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        "rm -f /etc/yum.repos.d/*.repo\n"
        f"printf %s {shlex.quote(repos_for(distro, mirrors))} > /etc/yum.repos.d/outpost-fetch.repo\n"
        # `dnf download` is a plugin on dnf 4 and built in on dnf 5 (Fedora 41+): install it where it is one
        "dnf -q -y install dnf-plugins-core >/dev/null 2>&1 || true\n"
        # the whole tree, whether or not this container already has a package
        f"dnf -q -y download --resolve --alldeps --destdir {OUT} {wanted}\n"
    )


def repos_for(distro: str, mirrors: Mirrors) -> str:
    """A yum.repos.d file naming the mirror's repositories for this distro.

    gpgcheck is off: dnf only downloads here and installs nothing, and Gitea re-signs what it serves.
    """
    family, major = split_rpm_distro(distro)
    mirror_key, paths = REPOSITORIES[family]
    base = getattr(mirrors, mirror_key)
    arch = versions.arch_names()["uname"]
    out = ""
    for i, path in enumerate(paths):
        out += f"[outpost-{i}]\nname=outpost-{i}\nbaseurl={base}/{path.format(major=major, arch=arch)}\nenabled=1\ngpgcheck=0\n\n"
    return out
