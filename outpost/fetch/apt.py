"""Debian/Ubuntu packages, downloaded inside a container of the matching distro.

apt needs the real distro to resolve dependencies, so this is the one fetch step that
requires Docker on the machine running `outpost fetch`. The whole dependency tree is
downloaded, not just what the container happens to lack, so the packages install on slim
images, full images and hosts alike.
"""

import shlex
import subprocess
from pathlib import Path

from .. import versions

DEBIAN = {"bullseye", "bookworm", "trixie", "forky", "sid"}
UBUNTU = {"focal", "jammy", "noble", "oracular", "plucky", "questing"}


def image_for(distro: str) -> str:
    if distro in DEBIAN:
        return f"debian:{distro}"
    if distro in UBUNTU:
        return f"ubuntu:{distro}"
    raise ValueError(f"apt distro '{distro}' is not a known Debian or Ubuntu codename")


def sources_for(distro: str, mirrors) -> str:
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


def script_for(distro: str, packages: list[str], mirrors) -> str:
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        "rm -f /etc/apt/sources.list.d/*\n"
        f"printf %s {shlex.quote(sources_for(distro, mirrors))} > /etc/apt/sources.list\n"
        "apt-get update -qq\n"
        "cd /out\n"
        # every package in the dependency tree, whether or not this container already has it
        f"tree=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances {wanted} "
        "| grep -E '^[a-z0-9]' | sort -u)\n"
        "apt-get download -qq $tree\n"
    )


def fetch(apt: dict[str, list[str]], box: Path, mirrors, log=print, run=subprocess.run) -> dict:
    result = {}
    for distro, packages in apt.items():
        out = box / "apt" / distro
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
            raise RuntimeError(f"apt download for {distro} failed:\n" + "\n".join(tail))
        count = len(list(out.glob("*.deb")))
        log(f"    {count} .deb file(s) in box/apt/{distro}")
        result[distro] = count
    return result
