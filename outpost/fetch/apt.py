"""Debian and Ubuntu packages, fetched inside a container of the matching distro.

apt needs the real distro to resolve dependencies. The whole dependency tree is downloaded, not
just what the container happens to lack, so the packages install on slim images, full images and
servers alike.
"""

import shlex
import subprocess
from pathlib import Path

from outpost.config import Mirrors
from outpost.distros import DEBIAN, UBUNTU
from outpost.fetch.in_container import OUT, OsPackageRecipe, fetch_os_packages


def fetch(apt: dict[str, list[str]], box: Path, mirrors: Mirrors, arch: str, log=print, run=subprocess.run) -> dict:
    """Fetch each distro's packages and their whole dependency tree into box/apt/<distro>.

    Returns:
        {distro: number of .deb files}.
    """
    recipe = OsPackageRecipe(
        image_for=image_for,
        script_for=lambda distro, packages: script_for(distro, packages, mirrors),
        shell="bash",
        file_suffix=".deb",
    )
    return fetch_os_packages("apt", apt, box, recipe, arch, log=log, run=run)


def image_for(distro: str) -> str:
    """bookworm -> debian:bookworm, noble -> ubuntu:noble.

    Raises:
        ValueError: distro is not a Debian or Ubuntu codename Outpost knows.
    """
    if distro in DEBIAN:
        return f"debian:{distro}"
    if distro in UBUNTU:
        return f"ubuntu:{distro}"
    raise ValueError(f"apt distro '{distro}' is not a known Debian or Ubuntu codename")


def script_for(distro: str, packages: list[str], mirrors: Mirrors) -> str:
    """The script that points apt at the mirror and downloads the packages' whole tree into /out."""
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        "rm -f /etc/apt/sources.list.d/*\n"
        f"printf %s {shlex.quote(sources_for(distro, mirrors))} > /etc/apt/sources.list\n"
        "apt-get update -qq\n"
        f"cd {OUT}\n"
        # every package in the dependency tree, whether or not this container already has it
        f"tree=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances {wanted} "
        "| grep -E '^[a-z0-9]' | sort -u)\n"
        "apt-get download -qq $tree\n"
    )


def sources_for(distro: str, mirrors: Mirrors) -> str:
    """sources.list lines for the distro, its updates and its security archive, from mirrors.yml."""
    if distro in DEBIAN:
        return (
            f"deb {mirrors.debian} {distro} main\n"
            f"deb {mirrors.debian} {distro}-updates main\n"
            f"deb {mirrors.debian_security} {distro}-security main\n"
        )
    return (
        f"deb {mirrors.ubuntu} {distro} main universe\n"
        f"deb {mirrors.ubuntu} {distro}-updates main universe\n"
        f"deb {mirrors.ubuntu} {distro}-security main universe\n"
    )
