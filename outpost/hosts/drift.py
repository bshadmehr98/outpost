"""version_drift(): the pieces a server runs at another version than the pinned one."""

from outpost import versions


def pinned_versions() -> dict[str, str]:
    """The pinned version of each piece Outpost installs. tinyproxy is not pinned: it is the distro's
    own package, at whatever version Gitea has."""
    return {
        "gitea": versions.GITEA,
        "docker": versions.DOCKER,
        "compose": versions.COMPOSE,
        "buildx": versions.BUILDX,
        "k3s": versions.K3S,
        "coredns": versions.COREDNS,
        "runner": versions.RUNNER,
    }


def version_drift(facts: dict) -> list[str]:
    """Installed pieces whose version differs from the pinned one, as 'docker 29.9.1 (pinned 29.9.2)'.

    k3s counts as the same when only the +k3sN suffix differs.
    """
    pinned = pinned_versions()
    drift = []
    for name, installed in (facts.get("installed") or {}).items():
        wanted = pinned.get(name)
        if wanted and installed and not _same_version(name, installed, wanted):
            drift.append(f"{name} {installed} (pinned {wanted})")
    return drift


def _same_version(name: str, installed: str, wanted: str) -> bool:
    if name == "k3s":
        return installed.split("+")[0] == wanted.split("+")[0]
    return installed == wanted
