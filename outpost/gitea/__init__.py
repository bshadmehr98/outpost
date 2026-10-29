"""Everything that talks to Gitea: install it, talk to its API, fill its package registries, back it up.

    gitea.setup(system)                              # install Gitea on its server, and seal the server
    gitea.upload(system, sources=[])                 # push the box into Gitea's registries
    gitea.backup(system); gitea.restore(system, archive)
    with gitea.connect(system) as client:            # a GiteaClient, through an ssh tunnel
        client.ensure_repo("pkgs", "demo")
        client.packages.upload_generic("tool", "1.0", Path("tool"))

Code outside this package calls `gitea.connect(system)` through the package (`from outpost import
gitea`), so tests replace one name to fake the API: `outpost.gitea.connect`.

    setup/            `outpost gitea setup`, in seven named steps (see setup/__init__.py)
    upload.py         upload(), upload_box(), push_config_image(), KIND_UPLOADERS: one uploader per kind
    backup.py         backup(), restore()
    connect.py        connect(), api_token(): the API through an ssh tunnel
    client.py         GiteaClient: version, organisations, repositories, tokens, secrets, runners, workflow runs
    packages.py       GiteaPackages (client.packages): upload_* per registry, signing keys, has_*, push_image
    upload_formats.py the shape of each upload (multipart, npm's package.json and publish body) and upload_outcome()
    service.py        paths on the server, GITEA_CLI, wait_for_gitea(), installed_version(), token_works()

Code outside this folder uses only the names below.
"""

from outpost.gitea.backup import backup, restore
from outpost.gitea.client import GiteaClient
from outpost.gitea.connect import api_token, connect
from outpost.gitea.packages import AlpineKey, GiteaPackages
from outpost.gitea.setup import setup
from outpost.gitea.upload import push_config_image, upload, upload_box
from outpost.gitea.upload_formats import EXISTS, UPLOADED, npm_package_json, pypi_name_version

__all__ = [
    "EXISTS",
    "UPLOADED",
    "AlpineKey",
    "GiteaClient",
    "GiteaPackages",
    "api_token",
    "backup",
    "connect",
    "npm_package_json",
    "push_config_image",
    "pypi_name_version",
    "restore",
    "setup",
    "upload",
    "upload_box",
]
