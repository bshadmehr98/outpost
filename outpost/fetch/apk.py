"""Alpine packages, fetched inside an `alpine:<branch>` container, like apt.py does for Debian.

apk resolves dependencies against the branch's own index. `apk fetch --recursive` writes the whole
dependency tree, including packages the container already has (musl, zlib...), so the files
install on any Alpine image or server of that branch. Branches are written the Alpine way, `v3.20`,
which is also what Gitea's Alpine registry calls them.
"""

import shlex
import subprocess
from pathlib import Path

from outpost.config import Mirrors
from outpost.distros import apk_branch_version
from outpost.fetch.in_container import OUT, OsPackageRecipe, fetch_os_packages

# apk's own name for the CPU type, used in the repository paths and by Gitea
ARCHES = {"amd64": "x86_64", "arm64": "aarch64"}


def fetch(apk: dict[str, list[str]], box: Path, mirrors: Mirrors, arch: str, log=print, run=subprocess.run) -> dict:
    """Fetch each branch's packages and their whole dependency tree into box/apk/<branch>.

    Returns:
        {branch: number of .apk files}.
    """
    recipe = OsPackageRecipe(
        image_for=image_for,
        script_for=lambda branch, packages: script_for(branch, packages, mirrors),
        shell="sh",  # alpine has no bash
        file_suffix=".apk",
    )
    return fetch_os_packages("apk", apk, box, recipe, arch, log=log, run=run)


def image_for(branch: str) -> str:
    """v3.20 -> alpine:3.20.

    Raises:
        ValueError: branch is not written like v3.20 (`edge` is not supported: its packages change under your feet).
    """
    return f"alpine:{apk_branch_version(branch)}"


def script_for(branch: str, packages: list[str], mirrors: Mirrors) -> str:
    """The script that points apk at the mirror and fetches the packages' whole tree into /out."""
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        f"printf %s {shlex.quote(repositories_for(branch, mirrors))} > /etc/apk/repositories\n"
        "apk update -q\n"
        # the whole tree, whether or not this container already has a package
        f"apk fetch --recursive -o {OUT} {wanted}\n"
    )


def repositories_for(branch: str, mirrors: Mirrors) -> str:
    """/etc/apk/repositories for the branch: main and community, from mirrors.yml."""
    return f"{mirrors.alpine}/{branch}/main\n{mirrors.alpine}/{branch}/community\n"
