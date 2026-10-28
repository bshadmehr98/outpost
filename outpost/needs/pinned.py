"""What Outpost itself adds to your needs: the pinned downloads, and tinyproxy for the egress proxy.

    needs = all_needs(system)     # the needs files + gitea, docker, k3s... + tinyproxy

The pinned downloads (versions.pinned_files) depend on the infrastructure types in servers.yml.
tinyproxy is the distro's own package, so it is fetched for the proxy host's distro: the one
servers.yml names (`egress_proxy.distro`), or the one `outpost hosts` saw on the server.
"""

from pathlib import Path

from outpost import versions
from outpost.config import ConfigError, Mirrors, ProxyConfig
from outpost.distros import is_apt_distro, os_family, rpm_distro_of_host, split_rpm_distro
from outpost.hosts import cached_facts
from outpost.needs.model import FileNeed, Needs, add_unique
from outpost.needs.needs_file import load_needs
from outpost.system import AirGappedSystem

PROXY_PACKAGE = "tinyproxy"


def all_needs(system: AirGappedSystem, skip_unknown_proxy_distro: bool = False) -> Needs:
    """Everything to fetch: the needs files, the pinned downloads for the infrastructure types in
    servers.yml, and tinyproxy for the egress proxy host's distro when servers.yml has an egress proxy.

    Args:
        skip_unknown_proxy_distro: leave tinyproxy out, instead of raising, while the proxy host's
            distro is unknown or wrong (the dashboard shows the rest).

    Raises:
        ConfigError: a needs file or mirrors.yml is wrong, or the proxy host's distro is unknown.
    """
    needs = needs_and_pinned_files(system.workspace.needs_dir, system.mirrors, system.servers.infra_types)
    proxy = system.servers.egress_proxy
    if proxy:
        facts = None if proxy.distro else cached_facts(system.workspace).get(proxy.host)
        try:
            add_proxy_package(needs, proxy, facts)
        except ConfigError:
            if not skip_unknown_proxy_distro:
                raise
    return needs


def needs_and_pinned_files(needs_dir: Path, mirrors: Mirrors, infra_types: set[str]) -> Needs:
    """The needs files and the pinned downloads, without tinyproxy. all_needs() starts from this; the
    dashboard uses it alone while servers.yml does not load.

    Raises:
        ConfigError: a needs file is wrong.
    """
    return add_pinned_files(load_needs(needs_dir), versions.pinned_files(mirrors, infra_types))


def add_pinned_files(needs: Needs, pinned_files: list[dict]) -> Needs:
    """Add the pinned downloads (gitea, docker, k3s..., from versions.pinned_files) to the files list."""
    add_unique(needs.files, [FileNeed(**f) for f in pinned_files])
    return needs


def add_proxy_package(needs: Needs, proxy: ProxyConfig | None, facts: dict | None) -> Needs:
    """Add tinyproxy to the apt or rpm needs of the proxy host's distro. Nothing without an egress proxy.

    Args:
        facts: what `outpost hosts` cached about the proxy host, used when servers.yml names no distro.

    Raises:
        ConfigError: the distro is unknown, wrong, or Alpine.
    """
    if proxy:
        kind, distro = proxy_package_need(proxy, facts)
        packages = getattr(needs, kind).setdefault(distro, [])
        add_unique(packages, [PROXY_PACKAGE])
    return needs


def proxy_package_need(proxy: ProxyConfig, facts: dict | None) -> tuple[str, str]:
    """(kind, distro) to fetch tinyproxy for: `egress_proxy.distro`, else what `outpost hosts` cached.

    Raises:
        ConfigError: the distro is unknown, wrong, or Alpine.
    """
    if proxy.distro:
        return _need_for_named_distro(proxy.distro)
    found = _need_for_host_facts(proxy.host, facts or {})
    if found:
        return found
    raise ConfigError(
        f"Outpost does not know which system the egress proxy host {proxy.host} runs, so it cannot fetch tinyproxy "
        "for it. Set `distro:` under egress_proxy in servers.yml (noble, bookworm, rocky9...) or run "
        "`outpost hosts --refresh` once so Outpost can ask the host."
    )


def _need_for_named_distro(distro: str) -> tuple[str, str]:
    if is_apt_distro(distro):
        return "apt", distro
    try:
        split_rpm_distro(distro)
    except ValueError:
        raise ConfigError(
            f"egress_proxy.distro {distro!r} is neither a Debian or Ubuntu codename (bookworm, noble) "
            "nor an rpm distro (rocky9, alma9, fedora40, stream9)"
        ) from None
    return "rpm", distro


def _need_for_host_facts(alias: str, facts: dict) -> tuple[str, str] | None:
    if not facts.get("id"):
        return None
    family = os_family(facts)
    if family == "apk":
        raise ConfigError(
            f"the egress proxy host {alias} runs Alpine, which Outpost does not support as a proxy host; "
            "put the proxy on a Debian, Ubuntu or RHEL-family host (egress_proxy.host)"
        )
    if family == "apt" and facts.get("codename"):
        return "apt", facts["codename"]
    distro = rpm_distro_of_host(facts) if family == "rpm" else None
    return ("rpm", distro) if distro else None
