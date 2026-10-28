"""Image references: what people write (python:3.12-slim), what a registry calls it, and where Gitea keeps it."""

import re
from dataclasses import dataclass

# Gitea's own check on a container image name, below the owner (routers/api/packages/container/container.go,
# go-gitea/gitea v28.2.0). Stricter than the OCI spec: one separator between alphanumerics, so `--` and `__` fail.
GITEA_IMAGE_NAME = re.compile(r"\A[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*\Z")


@dataclass(frozen=True)
class Ref:
    """One image at one tag, in full: registry, repository and tag."""

    registry: str  # docker.io, ghcr.io, gitea.internal
    repo: str  # library/python, grafana/grafana
    tag: str  # 3.12-slim

    def __str__(self) -> str:
        return f"{self.registry}/{self.repo}:{self.tag}"

    @property
    def short(self) -> str:
        """How people write it: python:3.12-slim, ghcr.io/org/app:1."""
        repo = self.repo
        if self.registry == "docker.io":
            return f"{repo[8:] if repo.startswith('library/') else repo}:{self.tag}"
        return f"{self.registry}/{repo}:{self.tag}"

    @property
    def safe_name(self) -> str:
        """short, made safe for a folder name: the image's folder in box/images/."""
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.short)

    @property
    def mirror_name(self) -> str:
        """The repository path inside the Gitea organisation: the original one, `library/python`, `bitnami/redis`.

        Every image lives under the one organisation next to pip, npm and apt, so the original owner has
        to stay in the name: `library/redis` (the official image) and `bitnami/redis` are different
        images and must not collide as `pkgs/redis`. Gitea accepts a nested path below the organisation
        (`pkgs/library/python`) and shows it as one package, so the original path is kept as it is and
        reading it back needs no separator: `ghcr.io/org/team/app` lands as `pkgs/org/team/app`.
        A joined name was tried first: `library--python` is valid OCI but Gitea rejects it (NAME_INVALID),
        and `library_python` is ambiguous because quay.io, GitLab and Harbor owners may contain `_`.
        """
        return self.repo


def parse_ref(ref: str) -> Ref:
    """python:3.12-slim -> docker.io/library/python:3.12-slim.

    Raises:
        ValueError: ref names a digest (@sha256:...); only tags are supported.
    """
    if "@" in ref:
        raise ValueError(f"{ref}: use a tag, not a digest")
    name, tag = ref.rsplit(":", 1) if ":" in ref.rsplit("/", 1)[-1] else (ref, "latest")
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "docker.io", name
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return Ref(registry=registry, repo=repo, tag=tag)


def gitea_ref(gitea, ref: Ref) -> str:
    """Where `ref` lives once uploaded: python:3.12-slim -> gitea.internal/pkgs/library/python:3.12-slim.

    `gitea` is a GiteaEndpoint (name and org). This is the name Dockerfiles, compose, stack and k8s files
    write; nothing on a node rewrites a plain Docker Hub name, and a sealed node rejects Docker Hub."""
    return f"{gitea.name}/{gitea.org}/{ref.mirror_name}:{ref.tag}"


def original_ref(name: str) -> str:
    """The inverse of `Ref.mirror_name`: library/python -> python, bitnami/redis -> bitnami/redis.

    `name` is the path below the organisation, without the tag. The registry is not kept in Gitea, so
    `org/app` reads back as the Docker Hub name; a need for another registry names it in full."""
    return name[len("library/") :] if name.startswith("library/") and name.count("/") == 1 else name
