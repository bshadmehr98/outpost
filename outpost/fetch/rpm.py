"""RPM packages for the RHEL family, downloaded inside a container of the matching distro, like apt.py.

dnf resolves dependencies against the distro's own metadata, so the real dnf of the real distro runs
in a throwaway container. `dnf download --resolve --alldeps` writes the whole dependency tree,
including packages the container already has (glibc, openssl-libs...), so the files install on a
minimal image, a full image or a host alike.

A distro is named `<family><major>`: rocky9, alma9, fedora40, stream9 (CentOS Stream). The stock
repo files of the container are replaced by ones built from mirrors.yml, so a blocked network
only needs the mirror keys changed, as for apt.
"""

import re
import shlex
import subprocess
from pathlib import Path

from .. import versions

# distro -> (image, mirror key, the repositories under that mirror, relative to it)
DISTROS = {
    "rocky": (
        "rockylinux",
        "rocky",
        ("{major}/BaseOS/{arch}/os", "{major}/AppStream/{arch}/os", "{major}/extras/{arch}/os"),
    ),
    "alma": (
        "almalinux",
        "alma",
        ("{major}/BaseOS/{arch}/os", "{major}/AppStream/{arch}/os", "{major}/extras/{arch}/os"),
    ),
    "fedora": ("fedora", "fedora", ("releases/{major}/Everything/{arch}/os", "updates/{major}/Everything/{arch}")),
    "stream": (
        "quay.io/centos/centos",
        "centos_stream",
        (
            "{major}-stream/BaseOS/{arch}/os",
            "{major}-stream/AppStream/{arch}/os",
            "{major}-stream/extras-common/{arch}/os",
        ),
    ),
}
# /etc/os-release ID of a host -> distro family above
OS_IDS = {"rocky": "rocky", "almalinux": "alma", "fedora": "fedora", "centos": "stream"}


def split(distro: str) -> tuple[str, str]:
    """rocky9 -> (rocky, 9); fedora40 -> (fedora, 40)."""
    m = re.fullmatch(r"(rocky|alma|fedora|stream)(\d+)", distro)
    if not m:
        raise ValueError(
            f"rpm distro '{distro}' is not one of rocky9, alma9, fedora40, stream9 (family + major version)"
        )
    return m.group(1), m.group(2)


def image_for(distro: str) -> str:
    family, major = split(distro)
    image = DISTROS[family][0]
    return f"{image}:stream{major}" if family == "stream" else f"{image}:{major}"


def distro_for_host(os_id: str, version_id: str) -> str | None:
    """What a host calls itself in /etc/os-release -> the distro key, or None for anything else."""
    family = OS_IDS.get(os_id)
    major = str(version_id).split(".")[0]
    return f"{family}{major}" if family and major.isdigit() else None


def repos_for(distro: str, mirrors) -> str:
    """A yum.repos.d file naming the mirror's repositories for this distro, gpgcheck off: we download, dnf
    does not install anything here, and Gitea re-signs what it serves."""
    family, major = split(distro)
    _image, mirror_key, paths = DISTROS[family]
    base = getattr(mirrors, mirror_key)
    arch = versions.names()["uname"]
    out = ""
    for i, path in enumerate(paths):
        out += f"[outpost-{i}]\nname=outpost-{i}\nbaseurl={base}/{path.format(major=major, arch=arch)}\nenabled=1\ngpgcheck=0\n\n"
    return out


def script_for(distro: str, packages: list[str], mirrors) -> str:
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        "rm -f /etc/yum.repos.d/*.repo\n"
        f"printf %s {shlex.quote(repos_for(distro, mirrors))} > /etc/yum.repos.d/outpost-fetch.repo\n"
        # `dnf download` is a plugin on dnf 4 and built in on dnf 5 (Fedora 41+): install it where it is one
        "dnf -q -y install dnf-plugins-core >/dev/null 2>&1 || true\n"
        # the whole tree, whether or not this container already has a package
        f"dnf -q -y download --resolve --alldeps --destdir /out {wanted}\n"
    )


def fetch(rpm: dict[str, list[str]], box: Path, mirrors, log=print, run=subprocess.run) -> dict:
    result = {}
    for distro, packages in rpm.items():
        out = box / "rpm" / distro
        out.mkdir(parents=True, exist_ok=True)
        image = image_for(distro)
        log(f"    {distro}: {len(packages)} package(s) via {image}")
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            f"linux/{versions.ARCH}",
            "-v",
            f"{out.resolve()}:/out",
            image,
            "bash",
            "-c",
            script_for(distro, packages, mirrors),
        ]
        proc = run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
            raise RuntimeError(f"rpm download for {distro} failed:\n" + "\n".join(tail))
        count = len(list(out.glob("*.rpm")))
        log(f"    {count} .rpm file(s) in box/rpm/{distro}")
        result[distro] = count
    return result
