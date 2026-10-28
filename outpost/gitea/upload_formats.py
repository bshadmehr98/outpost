"""The shapes Gitea's package registries want an upload in, and how to read their answer.

GiteaPackages (packages.py) builds its uploads with these:

    pypi_name_version("requests-2.31.0-py3-none-any.whl")    ("requests", "2.31.0")
    npm_package_json(Path("chalk-5.3.0.tgz"))                 the package.json inside the tarball
    body, content_type = multipart(fields, "content", name, data)
    upload_outcome(response, "pypi", name)                    "uploaded" or "exists"; else HttpError
"""

import json
import tarfile
import uuid
from pathlib import Path

from outpost.http import HttpError, Response

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


def multipart(fields: dict[str, str], file_field: str, filename: str, file_data: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with some fields and one file. Returns the body and its Content-Type."""
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    out += f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    out += file_data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


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
