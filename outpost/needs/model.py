"""The merged needs: what every needs file asks for, as one Needs."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileNeed:
    """One plain download: stored in the box as files/<name>/<version>/<filename>."""

    name: str
    version: str
    url: str
    filename: str


@dataclass(frozen=True)
class NpmNeed:
    """One npm package at an exact version, with its tarball URL and checksum when a lockfile says them."""

    name: str
    version: str
    resolved: str | None = None  # tarball URL from a lockfile, if known
    integrity: str | None = None  # sha512-... from a lockfile, if known


@dataclass
class Needs:
    """Everything to fetch, per kind. OS packages are grouped by the distro (or Alpine branch) they are for."""

    images: list[str] = field(default_factory=list)
    pypi: dict[str, list[str]] = field(default_factory=dict)  # python version -> pip specs
    npm: list[NpmNeed] = field(default_factory=list)
    apt: dict[str, list[str]] = field(default_factory=dict)  # distro -> packages
    apk: dict[str, list[str]] = field(default_factory=dict)  # alpine branch (v3.20) -> packages
    rpm: dict[str, list[str]] = field(default_factory=dict)  # rpm distro (rocky9) -> packages
    files: list[FileNeed] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # which needs files contributed

    def summary(self) -> dict[str, int]:
        """How many things of each kind."""
        return {
            "images": len(self.images),
            "pypi": sum(len(v) for v in self.pypi.values()),
            "npm": len(self.npm),
            "apt": sum(len(v) for v in self.apt.values()),
            "apk": sum(len(v) for v in self.apk.values()),
            "rpm": sum(len(v) for v in self.rpm.values()),
            "files": len(self.files),
        }

    def is_empty(self) -> bool:
        """True when nothing of any kind is needed."""
        return not any(self.summary().values())


def add_unique(target: list, items) -> None:
    """Append each item that target does not have yet, keeping the order."""
    for item in items:
        if item not in target:
            target.append(item)
