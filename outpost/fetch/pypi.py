"""Python packages via `pip download`, asking for Linux wheels for the Python version in the needs file.

pip resolves dependencies for the target platform when told to use wheels only. Packages that
ship no wheel fall back to an sdist, which builds anywhere.
"""

import subprocess
import sys
from pathlib import Path

from .. import versions


def pip_command() -> list[str]:
    for cmd in ([sys.executable, "-m", "pip"], ["pip3"], ["pip"]):
        if subprocess.run(cmd + ["--version"], capture_output=True).returncode == 0:
            return cmd
    raise RuntimeError("pip not found. Install it with: python3 -m ensurepip --upgrade")


def wheel_flags(python: str) -> list[str]:
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
    for p in versions.arch_names()["pip"]:
        flags += ["--platform", p]
    return flags


def fetch(pypi: dict[str, list[str]], box: Path, mirrors, log=print, run=subprocess.run) -> dict:
    out = box / "pypi"
    out.mkdir(parents=True, exist_ok=True)
    pip = pip_command() if run is subprocess.run else ["pip"]
    base = pip + ["download", "-q", "-d", str(out), "--index-url", mirrors.pypi, "--disable-pip-version-check"]
    failed: list[str] = []
    for python, specs in pypi.items():
        req = out / f".requirements-py{python}.txt"
        req.write_text("\n".join(specs) + "\n")
        log(f"    python {python}: {len(specs)} spec(s)")
        wheels = run(base + ["-r", str(req)] + wheel_flags(python), capture_output=True, text=True)
        if wheels.returncode == 0:
            continue
        log("    some packages have no wheel; retrying one by one with sdist fallback")
        for spec in specs:
            if run(base + [spec] + wheel_flags(python), capture_output=True, text=True).returncode == 0:
                continue
            sdist = run(base + [spec, "--no-deps", "--no-binary=:all:"], capture_output=True, text=True)
            if sdist.returncode != 0:
                failed.append(
                    f"{spec} (py{python}): {sdist.stderr.strip().splitlines()[-1] if sdist.stderr.strip() else 'pip failed'}"
                )
                log(f"    FAILED {spec}")
    count = len([p for p in out.iterdir() if p.suffix in (".whl", ".gz", ".zip")])
    log(f"    {count} file(s) in box/pypi, {len(failed)} failed")
    return {"files": count, "failed": failed}
