"""Turning a project folder, or a single lockfile, into a needs file. Used by the dashboard.

    proposal = scan("~/code/my-api", gitea="gitea.internal/pkgs")   # lockfiles, images, apt/apk/rpm packages
    write_needs(workspace, proposal)                                 # needs/my-api.yml, after the user edits it
    describe_lockfile(path)                                          # what one uploaded lockfile pins

Nothing here is clever. It finds the files you already have and reads them with the same parsers
`outpost fetch` uses, so what the page shows is what will be fetched.

    lockfiles.py      describe_lockfile(), PYTHON_LOCKFILES, JS_LOCKFILES
    dockerfiles.py    read_dockerfile(), needed_image(), distro_for_image(), ALPINE_CURRENT
    scan.py           scan(), python_version()
    write_needs.py    write_needs()
"""

from outpost.ui.projects.dockerfiles import ALPINE_CURRENT, distro_for_image, needed_image, read_dockerfile
from outpost.ui.projects.lockfiles import JS_LOCKFILES, PYTHON_LOCKFILES, describe_lockfile
from outpost.ui.projects.scan import python_version, scan
from outpost.ui.projects.write_needs import write_needs

__all__ = [
    "ALPINE_CURRENT",
    "JS_LOCKFILES",
    "PYTHON_LOCKFILES",
    "describe_lockfile",
    "distro_for_image",
    "needed_image",
    "python_version",
    "read_dockerfile",
    "scan",
    "write_needs",
]
