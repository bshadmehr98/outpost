"""Python packages via `pip download`, asking for Linux wheels for the Python version in the needs file.

pip resolves dependencies for the target platform when told to use wheels only. Packages that
ship no wheel fall back to an sdist, which builds anywhere. The specs of each Python version are
written to box/pypi/.requirements-py<version>.txt (upload and the dashboard skip dot files).
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

from outpost import versions
from outpost.config import Mirrors

logger = logging.getLogger(__name__)

PACKAGE_SUFFIXES = (".whl", ".gz", ".zip")  # wheels and sdists


def fetch(pypi: dict[str, list[str]], box: Path, mirrors: Mirrors, log=print, run=subprocess.run) -> dict:
    """Download wheels (or sdists) for each Python version's specs, and their dependencies, into box/pypi.

    Args:
        run: subprocess.run, or a test double (then pip is called as plain `pip`).

    Returns:
        {"files": number of packages in box/pypi, "failed": ["<spec> (py<version>): <why>", ...]}.
    """
    out = box / "pypi"
    out.mkdir(parents=True, exist_ok=True)
    pip = pip_command() if run is subprocess.run else ["pip"]
    download = pip + ["download", "-q", "-d", str(out), "--index-url", mirrors.pypi, "--disable-pip-version-check"]
    failed: list[str] = []
    for python, specs in pypi.items():
        requirements = out / f".requirements-py{python}.txt"
        requirements.write_text("\n".join(specs) + "\n")
        log(f"    python {python}: {len(specs)} spec(s)")
        if _pip(run, download + ["-r", str(requirements)] + wheel_flags(python)).returncode == 0:
            continue
        log("    some packages have no wheel; retrying one by one with sdist fallback")
        for spec in specs:
            why = _download_one(run, download, spec, python)
            if why:
                failed.append(f"{spec} (py{python}): {why}")
                log(f"    FAILED {spec}")
    count = len([p for p in out.iterdir() if p.suffix in PACKAGE_SUFFIXES])
    log(f"    {count} file(s) in box/pypi, {len(failed)} failed")
    return {"files": count, "failed": failed}


def pip_command() -> list[str]:
    """The first pip that runs: this Python's, then pip3, then pip.

    Raises:
        RuntimeError: there is no pip.
    """
    for cmd in ([sys.executable, "-m", "pip"], ["pip3"], ["pip"]):
        if subprocess.run(cmd + ["--version"], capture_output=True).returncode == 0:
            return cmd
    raise RuntimeError("pip not found. Install it with: python3 -m ensurepip --upgrade")


def wheel_flags(python: str) -> list[str]:
    """pip flags that ask for binary wheels for CPython <python> on Linux, for the box's CPU type."""
    abi = "cp" + python.replace(".", "")
    flags = [
        "--python-version",
        python,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "--abi",
        "abi3",
        "--abi",
        "none",
        "--only-binary=:all:",
    ]
    for platform in versions.arch_names()["pip"]:
        flags += ["--platform", platform]
    return flags


def _download_one(run, download: list[str], spec: str, python: str) -> str | None:
    """One spec: its wheel and dependencies, else its sdist alone. Returns why it failed, or None."""
    if _pip(run, download + [spec] + wheel_flags(python)).returncode == 0:
        return None
    sdist = _pip(run, download + [spec, "--no-deps", "--no-binary=:all:"])
    if sdist.returncode == 0:
        return None
    return sdist.stderr.strip().splitlines()[-1] if sdist.stderr.strip() else "pip failed"


def _pip(run, command: list[str]):
    started = time.monotonic()
    proc = run(command, capture_output=True, text=True)
    logger.debug("%s: exit %d (%.1fs)", " ".join(command[:3]), proc.returncode, time.monotonic() - started)
    return proc
