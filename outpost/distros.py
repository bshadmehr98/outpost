"""Linux distro names and families, in one place.

Outpost meets a distro in three ways: a needs file names one (`apt: {distro: bookworm}`), a server
says what it runs (/etc/os-release, cached by `outpost hosts`), and the box keeps one folder per
distro (box/apt/bookworm, box/apk/v3.20, box/rpm/rocky9). These tables and functions turn one into
the other.

    os_family({"id": "rocky", "id_like": "rhel centos fedora"})    -> "rpm"
    distro_of_host({"id": "debian", "codename": "bookworm"})       -> "bookworm"
    kind_for_distro("v3.20")                                       -> "apk"
    split_rpm_distro("fedora40")                                   -> ("fedora", "40")

The names, per kind:

    apt   a Debian or Ubuntu codename: bookworm, noble
    apk   an Alpine branch, the way Alpine and Gitea write it: v3.20
    rpm   <family><major>: rocky9, alma9, fedora40, stream9 (CentOS Stream)
"""

import re

DEBIAN = {"bullseye", "bookworm", "trixie", "forky", "sid"}
UBUNTU = {"focal", "jammy", "noble", "oracular", "plucky", "questing"}

# the rpm families Outpost knows, and the container image each one is fetched in
RPM_IMAGES = {
    "rocky": "rockylinux",
    "alma": "almalinux",
    "fedora": "fedora",
    "stream": "quay.io/centos/centos",
}
# /etc/os-release ID of a server -> the rpm family above
RPM_OS_IDS = {"rocky": "rocky", "almalinux": "alma", "fedora": "fedora", "centos": "stream"}

_RPM_DISTRO = re.compile(f"({'|'.join(RPM_IMAGES)})(\\d+)")
_APK_BRANCH = re.compile(r"v(\d+\.\d+)")


def os_family(facts: dict) -> str:
    """apt, apk or rpm, from /etc/os-release's ID and ID_LIKE.

    Anything unrecognised is treated as apt, which is what every node was assumed to be before
    Alpine and the RHEL family were supported.
    """
    ids = f"{facts.get('id', '')} {facts.get('id_like', '')}".lower().split()
    if "alpine" in ids:
        return "apk"
    if any(i in ("rhel", "fedora", "centos", "rocky", "almalinux") for i in ids):
        return "rpm"
    return "apt"


def distro_of_host(facts: dict) -> str | None:
    """A server's own distro, as the needs and the box name it: bookworm, v3.20, rocky9. None when unknown."""
    family = os_family(facts)
    if family == "apk":
        return apk_branch_of(facts)
    if family == "rpm":
        return rpm_distro_of_host(facts)
    return facts.get("codename") or None


def apk_branch_of(facts: dict) -> str | None:
    """An Alpine server's own branch: VERSION_ID 3.20.3 -> v3.20."""
    parts = str(facts.get("version_id", "")).split(".")
    return f"v{parts[0]}.{parts[1]}" if len(parts) >= 2 and all(x.isdigit() for x in parts[:2]) else None


def rpm_distro_of_host(facts: dict) -> str | None:
    """What an rpm server calls itself in /etc/os-release -> the rpm distro (rocky9), or None for anything else."""
    family = RPM_OS_IDS.get(facts.get("id", ""))
    major = str(facts.get("version_id", "")).split(".")[0]
    return f"{family}{major}" if family and major.isdigit() else None


def kind_for_distro(distro: str | None) -> str | None:
    """bookworm -> apt, v3.20 -> apk, rocky9 -> rpm; None for no distro."""
    if not distro:
        return None
    if is_apk_branch(distro):
        return "apk"
    if _RPM_DISTRO.fullmatch(distro):
        return "rpm"
    return "apt"


def is_apt_distro(distro: str) -> bool:
    """True for a Debian or Ubuntu codename Outpost knows."""
    return distro in DEBIAN or distro in UBUNTU


def is_apk_branch(branch: str) -> bool:
    """True for an Alpine branch written like v3.20 (`edge` is not one: its packages change under your feet)."""
    return bool(_APK_BRANCH.fullmatch(branch))


def apk_branch_version(branch: str) -> str:
    """v3.20 -> 3.20, the tag of the alpine image for that branch.

    Raises:
        ValueError: branch is not written like v3.20.
    """
    match = _APK_BRANCH.fullmatch(branch)
    if not match:
        raise ValueError(f"apk branch '{branch}' is not an Alpine branch like v3.20")
    return match.group(1)


def split_rpm_distro(distro: str) -> tuple[str, str]:
    """rocky9 -> (rocky, 9); fedora40 -> (fedora, 40).

    Raises:
        ValueError: distro is not one of the rpm families followed by a major version.
    """
    match = _RPM_DISTRO.fullmatch(distro)
    if not match:
        raise ValueError(
            f"rpm distro '{distro}' is not one of rocky9, alma9, fedora40, stream9 (family + major version)"
        )
    return match.group(1), match.group(2)
