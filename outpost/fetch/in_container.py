"""OS packages are fetched inside a throwaway container of the right distro: apt, apk and rpm share this.

A package manager resolves dependencies against its own distro's index, so the real apt, apk or
dnf of the real distro runs in `docker run --rm`, with the box folder mounted at /out. This is the
one part of `outpost fetch` that needs Docker on the laptop.

    fetch_os_packages("apt", {"bookworm": ["curl"]}, box, OsPackageRecipe(...), log=print)
"""

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from outpost import versions

logger = logging.getLogger(__name__)

OUT = "/out"  # where the box folder is mounted inside the container


@dataclass(frozen=True)
class OsPackageRecipe:
    """How one kind fetches its packages: which image, which shell, which script, which files it writes."""

    image_for: Callable[[str], str]  # distro -> container image
    script_for: Callable[[str, list[str]], str]  # distro, packages -> the script that writes them into OUT
    shell: str  # bash, or sh where the image has no bash (alpine)
    file_suffix: str  # .deb, .apk, .rpm


def fetch_os_packages(
    kind: str,
    packages_by_distro: dict[str, list[str]],
    box: Path,
    recipe: OsPackageRecipe,
    log=print,
    run=subprocess.run,
) -> dict:
    """Fetch each distro's packages into box/<kind>/<distro>, in a container of that distro.

    Returns:
        {distro: number of files in its folder}.

    Raises:
        RuntimeError: the package manager failed in the container (the last lines of its output).
    """
    result = {}
    for distro, packages in packages_by_distro.items():
        out_dir = box / kind / distro
        out_dir.mkdir(parents=True, exist_ok=True)
        image = recipe.image_for(distro)
        log(f"    {distro}: {len(packages)} package(s) via {image}")
        run_in_container(
            image,
            recipe.shell,
            recipe.script_for(distro, packages),
            out_dir,
            label=f"{kind} download for {distro}",
            run=run,
        )
        count = len(list(out_dir.glob(f"*{recipe.file_suffix}")))
        log(f"    {count} {recipe.file_suffix} file(s) in box/{kind}/{distro}")
        result[distro] = count
    return result


def run_in_container(image: str, shell: str, script: str, out_dir: Path, label: str, run=subprocess.run) -> None:
    """Run a script in a throwaway container of image, for the box's CPU type, with out_dir mounted at /out.

    Args:
        label: what the script does, for the error message ("apt download for bookworm").
        run: subprocess.run, or a test double.

    Raises:
        RuntimeError: the script failed; the message has the last lines of its output.
    """
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        f"linux/{versions.ARCH}",
        "-v",
        f"{out_dir.resolve()}:{OUT}",
        image,
        shell,
        "-c",
        script,
    ]
    started = time.monotonic()
    proc = run(command, capture_output=True, text=True)
    logger.debug("docker run %s: %s exit %d (%.1fs)", image, label, proc.returncode, time.monotonic() - started)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"{label} failed:\n" + "\n".join(tail))
