"""Gitea's package registries: upload into each one, read its signing key, ask what it has.

Package registries we fill:                 Clients read them at:
  generic  files (binaries, tarballs)       https://gitea/api/packages/ORG/generic/NAME/VERSION/FILE
  pypi     wheels and sdists                https://gitea/api/packages/ORG/pypi/simple
  npm      tarballs                         https://gitea/api/packages/ORG/npm/
  debian   .deb files                       deb https://gitea/api/packages/ORG/debian DISTRO main
  alpine   .apk files                       https://gitea/api/packages/ORG/alpine/BRANCH/main   (in /etc/apk/repositories)
  rpm      .rpm files                       baseurl=https://gitea/api/packages/ORG/rpm/GROUP     (one group per distro)
  container images                          docker pull gitea/OWNER/IMAGE:TAG

The Alpine and RPM endpoints follow the Gitea docs (https://docs.gitea.com/usage/packages/alpine and
https://docs.gitea.com/usage/packages/rpm): PUT /api/packages/{owner}/alpine/{branch}/{repository} and
PUT /api/packages/{owner}/rpm/{group}/upload (or /rpm/upload without a group), 201 when stored and
409 when the same file is already there; the signing keys are GET /alpine/key and GET /rpm/repository.key.

A generic, Debian, Alpine or RPM upload is the file itself, streamed from disk. A PyPI upload is a
multipart form and an npm upload a JSON document with the tarball in base64, so those two are read
into memory; wheels and npm tarballs are small.
"""

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from outpost import oci
from outpost.gitea.upload_formats import multipart, npm_package_json, pypi_name_version, upload_outcome
from outpost.http import Http, sha256_file

ALPINE_KEY_DEFAULT_NAME = "{owner}.rsa.pub"  # only if Gitea sends no Content-Disposition; apk would then need a rename
OCTET_STREAM = {"Content-Type": "application/octet-stream"}


class AlpineKey(NamedTuple):
    """Gitea's Alpine signing key and the file name apk needs it under. apk only trusts an APKINDEX whose
    signature is named after a file in /etc/apk/keys/, and Gitea signs with `<owner>@<fingerprint>.rsa.pub`,
    which it reports in the Content-Disposition of /alpine/key; so the name travels with the key."""

    name: str
    content: str


class GiteaPackages:
    """The package registries of one Gitea organisation. Each upload_* returns "uploaded" or "exists"."""

    def __init__(self, http: Http, org: str, public_url: str):
        """
        Args:
            http: logged in to Gitea.
            org: the organisation that owns the packages, unless a call names another owner.
            public_url: Gitea's URL as the nodes see it, written into npm's tarball links.
        """
        self.http = http
        self.org = org
        self.public_url = public_url

    # -- generic ------------------------------------------------------------------
    def upload_generic(self, name: str, version: str, path: Path, owner: str | None = None) -> str:
        """Upload one file as generic/<name>/<version>/<file name>."""
        url = f"/api/packages/{owner or self.org}/generic/{quote(name)}/{quote(version)}/{quote(path.name)}"
        response = self.http.request("PUT", url, data=path, headers=OCTET_STREAM)
        return upload_outcome(response, "generic", f"{name} {version} {path.name}")

    # -- pypi ---------------------------------------------------------------------
    def upload_pypi(self, path: Path, owner: str | None = None) -> str:
        """Upload a wheel or an sdist, the way twine does."""
        name, version = pypi_name_version(path.name)
        fields = {
            ":action": "file_upload",
            "protocol_version": "1",
            "name": name,
            "version": version,
            "sha256_digest": sha256_file(path),
            "filetype": "bdist_wheel" if path.suffix == ".whl" else "sdist",
        }
        body, content_type = multipart(fields, "content", path.name, path.read_bytes())
        response = self.http.request(
            "POST", f"/api/packages/{owner or self.org}/pypi", data=body, headers={"Content-Type": content_type}
        )
        return upload_outcome(response, "pypi", path.name)

    # -- npm ----------------------------------------------------------------------
    def upload_npm(self, path: Path, owner: str | None = None) -> str:
        """Upload an npm tarball, the way `npm publish` does."""
        data = path.read_bytes()
        package = npm_package_json(path)
        name, version = package["name"], package["version"]
        owner = owner or self.org
        version_meta = {
            **package,
            "dist": {
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode(),
                "shasum": hashlib.sha1(data).hexdigest(),
                "tarball": f"{self.public_url}/api/packages/{owner}/npm/{quote(name, safe='@')}/-/{version}/{path.name}",
            },
        }
        document = {
            "_id": name,
            "name": name,
            "description": package.get("description", ""),
            "dist-tags": {"latest": version},
            "versions": {version: version_meta},
            "_attachments": {
                path.name: {
                    "content_type": "application/octet-stream",
                    "data": base64.b64encode(data).decode(),
                    "length": len(data),
                }
            },
        }
        url = f"/api/packages/{owner}/npm/{quote(name, safe='@')}"
        response = self.http.request(
            "PUT", url, data=json.dumps(document).encode(), headers={"Content-Type": "application/json"}
        )
        return upload_outcome(response, "npm", f"{name}@{version}")

    # -- debian -------------------------------------------------------------------
    def upload_debian(self, path: Path, distro: str, component: str = "main", owner: str | None = None) -> str:
        """Upload a .deb into one suite (the apt distro, such as bookworm)."""
        url = f"/api/packages/{owner or self.org}/debian/pool/{quote(distro)}/{quote(component)}/upload"
        response = self.http.request("PUT", url, data=path, headers=OCTET_STREAM)
        return upload_outcome(response, "debian", path.name)

    def debian_key(self, owner: str | None = None) -> str:
        """The PGP key Gitea signs its Debian repository with."""
        return self.http.get(f"/api/packages/{owner or self.org}/debian/repository.key", ok=(200,)).text

    def debian_has_distro(self, distro: str, owner: str | None = None) -> bool:
        """True when the Debian registry has a suite for distro."""
        return self.http.get(f"/api/packages/{owner or self.org}/debian/dists/{quote(distro)}/Release").status == 200

    # -- alpine -------------------------------------------------------------------
    def upload_alpine(self, path: Path, branch: str, repository: str = "main", owner: str | None = None) -> str:
        """PUT /api/packages/{owner}/alpine/{branch}/{repository}; Gitea reads the architecture from the package."""
        url = f"/api/packages/{owner or self.org}/alpine/{quote(branch)}/{quote(repository)}"
        response = self.http.request("PUT", url, data=path, headers=OCTET_STREAM)
        return upload_outcome(response, "alpine", f"{branch}/{repository} {path.name}")

    def alpine_key(self, owner: str | None = None) -> AlpineKey:
        """The RSA public key Gitea signs its APKINDEX files with, and the file name apk must store it under."""
        owner = owner or self.org
        response = self.http.get(f"/api/packages/{owner}/alpine/key", ok=(200,))
        disposition = next((v for k, v in response.headers.items() if k.lower() == "content-disposition"), "")
        found = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
        name = found.group(1).strip() if found else ALPINE_KEY_DEFAULT_NAME.format(owner=owner)
        return AlpineKey(name=name.rsplit("/", 1)[-1], content=response.text)

    def alpine_has_branch(self, branch: str, arch: str, repository: str = "main", owner: str | None = None) -> bool:
        """True when the Alpine registry has an index for branch/repository/arch."""
        url = f"/api/packages/{owner or self.org}/alpine/{quote(branch)}/{quote(repository)}/{quote(arch)}/APKINDEX.tar.gz"
        return self.http.get(url).status == 200

    # -- rpm ----------------------------------------------------------------------
    def upload_rpm(self, path: Path, group: str = "", owner: str | None = None) -> str:
        """PUT /api/packages/{owner}/rpm/{group}/upload, or /rpm/upload with no group. A group is a repository of
        its own in Gitea; Outpost uses one group per distro so rocky9 and fedora40 packages never mix."""
        url = f"/api/packages/{owner or self.org}/rpm{_group_path(group)}/upload"
        response = self.http.request("PUT", url, data=path, headers=OCTET_STREAM)
        return upload_outcome(response, "rpm", f"{group or 'no group'} {path.name}")

    def rpm_key(self, owner: str | None = None) -> str:
        """The PGP key Gitea signs its RPM repository metadata (and re-signs uploaded packages) with."""
        return self.http.get(f"/api/packages/{owner or self.org}/rpm/repository.key", ok=(200,)).text

    def rpm_has_group(self, group: str, owner: str | None = None) -> bool:
        """True when the RPM registry has metadata for group."""
        return (
            self.http.get(f"/api/packages/{owner or self.org}/rpm{_group_path(group)}/repodata/repomd.xml").status
            == 200
        )

    # -- container images ---------------------------------------------------------
    def registry(self) -> oci.Registry:
        """A container-registry view of the same Gitea. Tokens come from /v2/token with our Basic auth."""
        return oci.Registry(self.http, token_url=f"{self.http.base}/v2/token")

    def push_image(self, layout: Path, owner: str, image: str, tag: str) -> oci.Pushed:
        """Push an OCI layout as <owner>/<image>:<tag>. The result says whether Gitea got anything new."""
        return self.registry().push(layout, f"{owner}/{image}", tag)


def _group_path(group: str) -> str:
    return f"/{quote(group, safe='/')}" if group else ""
