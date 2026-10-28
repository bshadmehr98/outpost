"""What your projects need. One YAML file per project in needs/, merged into one Needs.

    needs = all_needs(system)            # the needs files + the pinned downloads + tinyproxy
    needs = load_needs(workspace.needs_dir)   # only the needs files
    needs.images, needs.pypi["3.12"], needs.apt["bookworm"], needs.summary()

A needs file:

    name: my-api
    python: "3.12"                 # which Python the wheels are for (quote it: 3.10 is not 3.1)
    images: [python:3.12-slim, postgres:16]
    pypi:
      - file: requirements.txt     # or poetry.lock, uv.lock, Pipfile.lock
      - httpx==0.27.0              # or a plain pip spec
    npm:
      - file: package-lock.json    # or pnpm-lock.yaml, yarn.lock
      - chalk@5.3.0                # exact name@version (dependencies are not followed)
    apt:
      distro: bookworm             # must match the image or server that will install them
      packages: [libpq-dev, curl]
    apk:
      branch: v3.20                # Alpine branch of the image or server (alpine:3.20 -> v3.20)
      packages: [curl]
    rpm:
      distro: rocky9               # rocky9 | alma9 | fedora40 | stream9
      packages: [curl]
    files:
      - url: https://github.com/x/y/releases/download/v1.0/tool
        name: tool
        version: "1.0"

    model.py              FileNeed, NpmNeed, Needs
    needs_file.py         load_needs(), read_needs_file(): one reader per kind
    pinned.py             all_needs(), needs_and_pinned_files(), add_pinned_files(), add_proxy_package(), proxy_package_need()
    python_lockfiles.py   requirements, poetry, uv, Pipfile -> pip specs
    js_lockfiles.py       package-lock, pnpm, yarn -> NpmNeed

Code outside this folder uses only the names below.
"""

from outpost.needs.js_lockfiles import read_js_lockfile
from outpost.needs.model import FileNeed, Needs, NpmNeed
from outpost.needs.needs_file import load_needs, read_needs_file
from outpost.needs.pinned import (
    PROXY_PACKAGE,
    add_pinned_files,
    add_proxy_package,
    all_needs,
    needs_and_pinned_files,
    proxy_package_need,
)
from outpost.needs.python_lockfiles import read_python_lockfile

__all__ = [
    "PROXY_PACKAGE",
    "FileNeed",
    "Needs",
    "NpmNeed",
    "add_pinned_files",
    "add_proxy_package",
    "all_needs",
    "load_needs",
    "needs_and_pinned_files",
    "proxy_package_need",
    "read_js_lockfile",
    "read_needs_file",
    "read_python_lockfile",
]
