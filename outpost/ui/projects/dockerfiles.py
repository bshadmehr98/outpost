"""What a Dockerfile asks for: the images it builds FROM and the apt, apk and rpm packages it installs,
and which distro an image is built on."""

import re
from pathlib import Path

from outpost import oci
from outpost.distros import RPM_IMAGES

# the codenames looked for in an image tag (python:3.12-slim-bullseye), and Ubuntu's version tags
TAG_CODENAMES = ("bookworm", "bullseye", "trixie", "noble", "jammy", "focal")
UBUNTU_VERSIONS = {"24.04": "noble", "22.04": "jammy", "20.04": "focal"}
# `node:20-alpine` names no branch: it is whatever Alpine was current when the image was built. Outpost
# proposes the branch its own demo pins (alpine:3.20); bump the two together.
ALPINE_CURRENT = "v3.20"
# rockylinux -> rocky, ...; CentOS Stream's image is named centos and read by its tag
RPM_FAMILY_OF_IMAGE = {image: family for family, image in RPM_IMAGES.items() if "/" not in image}


# `RUN apt-get install ...`, `apk add ...`, `dnf install ...` (also yum, microdnf): the kind and the install verb
INSTALL_COMMANDS = (
    ("apt", r"apt-get\s+(?:-\S+\s+)*install\s+(.*?)(?:&&|;|\n|$)"),
    ("apk", r"\bapk\s+(?:-\S+\s+)*add\s+(.*?)(?:&&|;|\n|$)"),
    ("rpm", r"\b(?:dnf|yum|microdnf)\s+(?:-\S+\s+)*install\s+(.*?)(?:&&|;|\n|$)"),
)


def read_dockerfile(path: Path, gitea: str | None = None) -> dict:
    """The images and OS packages (apt, apk, rpm) a Dockerfile asks for. Build stages and ${ARGS} are left out.

    Args:
        gitea: `<name>/<org>`, so a literal Gitea name is read back into its need (see needed_image).

    Returns:
        {"path", "images": [...], "apt": [...], "apk": [...], "rpm": [...]}
    """
    text = path.read_text(errors="replace")
    return {"path": str(path), "images": _base_images(text, gitea), **_installed_packages(text)}


def _base_images(text: str, gitea: str | None) -> list[str]:
    """The needs behind the FROM lines, without build stages, scratch and duplicates."""
    stages: set[str] = set()
    images: list[str] = []
    for m in re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", text, re.I | re.M):
        image, stage = m.group(1), m.group(2)
        if stage:
            stages.add(stage.lower())
        if image.lower() in stages or image.lower() == "scratch":
            continue
        need = needed_image(image, gitea)
        if need and need not in images:
            images.append(need)
    return images


def _installed_packages(text: str) -> dict[str, list[str]]:
    """{kind: packages} from the install commands, with continued lines joined."""
    joined = re.sub(r"\\\s*\n", " ", text)
    found: dict[str, list[str]] = {}
    for kind, pattern in INSTALL_COMMANDS:
        packages: list[str] = []
        for m in re.finditer(pattern, joined):
            for word in m.group(1).split():
                if word.startswith("-") or word.split("=")[0].isupper():  # a flag, or VAR=value
                    continue
                word = word.split("=")[0]  # package=version pins the version; we fetch the distro's current one
                if re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", word) and word not in packages:
                    packages.append(word)
        found[kind] = packages
    return found


def needed_image(image: str, gitea: str | None = None) -> str | None:
    """The needs entry behind a FROM line, or None when it cannot be known.

    `${GITEA}/${ORG}/library/python:3.12-slim` and `gitea.internal/pkgs/library/python:3.12-slim` are what a
    converted Dockerfile says; the need that put them there is `python:3.12-slim`. `bitnami/redis:7` below the
    organisation is `bitnami/redis:7`. `gitea` is `<name>/<org>` (gitea.internal/pkgs) when the workspace knows
    it; without it only the `${GITEA}/${ORG}/...` form is read back. A `${BASE}` that hides the name is skipped."""
    tail = image.rsplit("/", 1)[-1]
    name = tail.partition(":")[0]
    if name == "config" and "/" in image:  # Outpost's own config image, not something to fetch
        return None
    if gitea and image.startswith(gitea.rstrip("/") + "/"):
        return oci.original_ref(image[len(gitea.rstrip("/")) + 1 :])
    if "$" in image:  # ${GITEA}/${ORG}/library/python:3.12-slim -> python:3.12-slim, if the rest stands on its own
        parts = image.split("/")
        while parts and "$" in parts[0]:
            parts.pop(0)
        rest = "/".join(parts)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*:[A-Za-z0-9._-]+", rest):
            return None
        return oci.original_ref(rest)
    return image


def distro_for_image(image: str) -> str | None:
    """`python:3.12-slim` -> bookworm, `ubuntu:24.04` -> noble (apt); `alpine:3.20` and `python:3.12-alpine3.20`
    -> v3.20, `node:20-alpine` -> ALPINE_CURRENT (apk); `rockylinux:9` -> rocky9, `fedora:40` -> fedora40 (rpm);
    unknown -> None. kind_for_distro() says which kind a result belongs to."""
    name, _, tag = image.partition(":")
    name = name.rsplit("/", 1)[-1]
    m = re.search(r"alpine(\d+\.\d+)?", tag) if name != "alpine" else re.match(r"(\d+\.\d+)", tag)
    if name == "alpine" or (m and "alpine" in tag):
        return f"v{m.group(1)}" if m and m.group(1) else ALPINE_CURRENT
    if name in RPM_FAMILY_OF_IMAGE:
        major = tag.split(".")[0].split("-")[0]
        return f"{RPM_FAMILY_OF_IMAGE[name]}{major}" if major.isdigit() else None
    if name == "centos":
        m = re.match(r"stream(\d+)", tag)
        return f"stream{m.group(1)}" if m else None
    for codename in TAG_CODENAMES:
        if codename in tag or (name == "debian" and tag.startswith(codename)):
            return codename
    if name == "ubuntu":
        return UBUNTU_VERSIONS.get(tag.split("-")[0]) or (tag if tag.isalpha() else None)
    if name in ("python", "node", "debian", "postgres", "nginx", "redis") and "alpine" not in tag:
        return "bookworm"  # Debian's current stable, which these official images build on
    return None
