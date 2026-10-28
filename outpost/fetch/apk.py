"""Alpine packages, downloaded inside an `alpine:<branch>` container, like apt.py does for Debian.

apk resolves dependencies against the branch's own index, so the real apk of the real branch runs
in a throwaway container. `apk fetch --recursive` writes the whole dependency tree, including
packages the container already has (musl, zlib...), so the files install on any Alpine image or
host of that branch. Branches are written the Alpine way, `v3.20`, which is also what Gitea's
Alpine registry calls them.
"""

import re
import shlex
import subprocess
from pathlib import Path

from .. import versions

# apk's own name for the CPU architecture, used in the repository paths and by Gitea
ARCHES = {"amd64": "x86_64", "arm64": "aarch64"}


def image_for(branch: str) -> str:
    """v3.20 -> alpine:3.20; `edge` is not supported (its packages change under your feet)."""
    m = re.fullmatch(r"v(\d+\.\d+)", branch)
    if not m:
        raise ValueError(f"apk branch '{branch}' is not an Alpine branch like v3.20")
    return f"alpine:{m.group(1)}"


def repositories_for(branch: str, mirrors) -> str:
    return f"{mirrors.alpine}/{branch}/main\n{mirrors.alpine}/{branch}/community\n"


def script_for(branch: str, packages: list[str], mirrors) -> str:
    wanted = " ".join(shlex.quote(p) for p in packages)
    return (
        "set -e\n"
        f"printf %s {shlex.quote(repositories_for(branch, mirrors))} > /etc/apk/repositories\n"
        "apk update -q\n"
        # the whole tree, whether or not this container already has a package
        f"apk fetch --recursive -o /out {wanted}\n"
    )


def fetch(apk: dict[str, list[str]], box: Path, mirrors, log=print, run=subprocess.run) -> dict:
    result = {}
    for branch, packages in apk.items():
        out = box / "apk" / branch
        out.mkdir(parents=True, exist_ok=True)
        image = image_for(branch)
        log(f"    {branch}: {len(packages)} package(s) via {image}")
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            f"linux/{versions.ARCH}",
            "-v",
            f"{out.resolve()}:/out",
            image,
            "sh",
            "-c",
            script_for(branch, packages, mirrors),
        ]
        proc = run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
            raise RuntimeError(f"apk download for {branch} failed:\n" + "\n".join(tail))
        count = len(list(out.glob("*.apk")))
        log(f"    {count} .apk file(s) in box/apk/{branch}")
        result[branch] = count
    return result
