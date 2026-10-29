"""The shapes Gitea's package registries want an upload in, and how to read their answer.

GiteaPackages (packages.py) builds its uploads with these:

    pypi_name_version("requests-2.31.0-py3-none-any.whl")    ("requests", "2.31.0")
    npm_package_json(Path("chalk-5.3.0.tgz"))                 the package.json inside the tarball
    body, content_type = multipart(fields, "content", path)   a StreamedBody: the file is read as it is sent
    npm_publish_body(package, path, tarball_url)              the `npm publish` document, also streamed
    upload_outcome(response, "pypi", name)                    "uploaded" or "exists"; else HttpError

The file of a PyPI or npm upload is never read into memory whole: the body is a StreamedBody of the
form's or document's text around the file (base64-encoded, for npm).
"""

import base64
import hashlib
import json
import tarfile
import uuid
from pathlib import Path

from outpost.http import CHUNK, Base64File, HttpError, Response, StreamedBody

UPLOADED = "uploaded"
EXISTS = "exists"


def upload_outcome(response: Response, kind: str, what: str) -> str:
    """UPLOADED or EXISTS, from the status of an upload.

    Raises:
        HttpError: Gitea refused the upload; the message has its words.
    """
    if response.status in (200, 201):
        return UPLOADED
    if response.status == 409 or (response.status == 400 and "exist" in response.text.lower()):
        return EXISTS
    raise HttpError(response.status, response.text, f"{kind} upload of {what}")


def multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[StreamedBody, str]:
    """A multipart/form-data body with some fields and one file, streamed from disk. Returns the body
    and its Content-Type."""
    boundary = uuid.uuid4().hex
    head = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
        for key, value in fields.items()
    )
    head += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    tail = f"\r\n--{boundary}--\r\n"
    return StreamedBody([head.encode(), path, tail.encode()]), f"multipart/form-data; boundary={boundary}"


def npm_publish_body(package: dict, path: Path, tarball_url: str) -> StreamedBody:
    """The JSON document `npm publish` sends: the package.json, its dist hashes, and the tarball in base64.

    Args:
        package: the package.json inside the tarball (npm_package_json).
        path: the tarball.
        tarball_url: where the nodes will download it from Gitea.
    """
    name, version = package["name"], package["version"]
    sha512, sha1 = _sha512_and_sha1(path)
    tarball_here = "OUTPOST-TARBALL-BASE64"  # stands in for the base64 text, which is streamed in its place
    document = {
        "_id": name,
        "name": name,
        "description": package.get("description", ""),
        "dist-tags": {"latest": version},
        "versions": {
            version: {
                **package,
                "dist": {
                    "integrity": "sha512-" + base64.b64encode(sha512).decode(),
                    "shasum": sha1.hex(),
                    "tarball": tarball_url,
                },
            }
        },
        "_attachments": {
            path.name: {
                "content_type": "application/octet-stream",
                "data": tarball_here,
                "length": path.stat().st_size,
            }
        },
    }
    before, after = json.dumps(document).split(tarball_here)  # base64 needs no JSON escaping
    return StreamedBody([before.encode(), Base64File(path), after.encode()])


def pypi_name_version(filename: str) -> tuple[str, str]:
    """requests-2.31.0-py3-none-any.whl -> (requests, 2.31.0);  Flask-3.0.0.tar.gz -> (Flask, 3.0.0).

    Raises:
        ValueError: the file is not a wheel or an sdist.
    """
    if filename.endswith(".whl"):
        name, version = filename[:-4].split("-")[:2]
        return name, version
    for ext in (".tar.gz", ".zip", ".tar.bz2"):
        if filename.endswith(ext):
            stem = filename[: -len(ext)]
            name, _, version = stem.rpartition("-")
            return name, version
    raise ValueError(f"{filename}: not a wheel or sdist")


def npm_package_json(path: Path) -> dict:
    """The package/package.json inside an npm tarball.

    Raises:
        ValueError: the tarball has none.
    """
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/package.json") and member.name.count("/") == 1:
                return json.loads(tar.extractfile(member).read())  # pyright: ignore[reportOptionalMemberAccess]  package.json is a file, never None
    raise ValueError(f"{path.name}: no package/package.json inside")


def _sha512_and_sha1(path: Path) -> tuple[bytes, bytes]:
    """The sha512 and sha1 digests of a file, read in chunks (npm's dist.integrity and dist.shasum)."""
    sha512, sha1 = hashlib.sha512(), hashlib.sha1()
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            sha512.update(chunk)
            sha1.update(chunk)
    return sha512.digest(), sha1.digest()
