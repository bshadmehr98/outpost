"""The files that point pip, npm, apt, apk and dnf at Gitea, shared by the nodes and the config image."""

from typing import TYPE_CHECKING

from outpost.client_config.paths import (
    PATH_APK_KEYS,
    PATH_APK_REPOS,
    PATH_APT_KEY,
    PATH_APT_LIST,
    PATH_CA,
    PATH_PROXY_ENV,
    PATH_RPM_KEY,
    PATH_RPM_REPO,
    STOCK_RPM_REPOS,
)
from outpost.client_config.proxy_settings import proxy_env

if TYPE_CHECKING:  # only for the type hints: endpoints imports PRIVATE_NETS from this package
    from outpost.endpoints import GiteaEndpoint, ProxyEndpoint


def client_files(
    gitea: "GiteaEndpoint",
    ca_pem: str,
    apt_key: str | None,
    distros: list[str],
    apk_key=None,
    apk_branches: list[str] | None = None,
    rpm_key: str | None = None,
    rpm_distros: list[str] | None = None,
    proxy: "ProxyEndpoint | None" = None,
) -> dict[str, str]:
    """The client files, as path -> content. Shared by nodes and the config image.

    A package source is written only for what Gitea has, and its key with it. With a `proxy`,
    /etc/outpost/proxy.env is added; without one, nothing about a proxy is written.

    Args:
        apt_key: Gitea's Debian key, or None.
        distros: the apt suites in Gitea.
        apk_key: an AlpineKey (name, content). The file under /etc/apk/keys must carry the name Gitea
            signed with. It is written only when a branch exists: apk rejects an index whose key it does
            not have, but accepts a missing key folder.
        apk_branches: the Alpine branches in Gitea.
        rpm_key: Gitea's RPM key, or None.
        rpm_distros: the RPM groups in Gitea.
        proxy: the egress proxy, when there is one.
    """
    npmrc = f"registry={gitea.npm_registry}\ncafile={PATH_CA}\n"
    files = {
        PATH_CA: ca_pem,
        "/etc/pip.conf": f"[global]\nindex-url = {gitea.pypi_index}\ncert = {PATH_CA}\n",
        "/etc/npmrc": npmrc,
        "/usr/local/etc/npmrc": npmrc,  # npm reads the file under its prefix; node images use /usr/local
        "/etc/apt/apt.conf.d/99outpost": apt_trusts_the_ca(),
    }
    if apt_key:
        files[PATH_APT_KEY] = apt_key
    if distros:
        files[PATH_APT_LIST] = "".join(apt_source_line(gitea, d) + "\n" for d in distros)
    if apk_branches:
        files[PATH_APK_REPOS] = "".join(apk_repository_line(gitea, b) + "\n" for b in apk_branches)
        if apk_key:
            files[f"{PATH_APK_KEYS}/{apk_key.name}"] = apk_key.content
    if rpm_distros:
        files[PATH_RPM_REPO] = "\n".join(rpm_repo_section(gitea, d) for d in rpm_distros)
        if rpm_key:
            files[PATH_RPM_KEY] = rpm_key
    if proxy:
        files[PATH_PROXY_ENV] = proxy_env(proxy)
    return files


def apt_trusts_the_ca() -> str:
    """/etc/apt/apt.conf.d/99outpost: apt checks Gitea's https certificate against the CA."""
    return f'Acquire::https::CaInfo "{PATH_CA}";\n'


def apt_source_line(gitea: "GiteaEndpoint", distro: str) -> str:
    """One line of outpost.list: Gitea's Debian registry for one suite."""
    return f"deb [signed-by={PATH_APT_KEY}] {gitea.apt_repo} {distro} main"


def apk_repository_line(gitea: "GiteaEndpoint", branch: str, repository: str = "main") -> str:
    """One line of /etc/apk/repositories: Gitea's Alpine registry for one branch."""
    return f"{gitea.apk_repo}/{branch}/{repository}"


def rpm_repo_section(gitea: "GiteaEndpoint", distro: str) -> str:
    """One [section] of outpost.repo. Gitea serves one RPM repository per group, and the group is the distro.

    gpgcheck stays on: Gitea signs the repository metadata and the packages it serves with the key at gpgkey.
    """
    return (
        f"[outpost-{distro}]\nname=Outpost {distro} (Gitea)\nbaseurl={gitea.rpm_repo}/{distro}\nenabled=1\n"
        f"gpgcheck=1\nrepo_gpgcheck=0\ngpgkey=file://{PATH_RPM_KEY}\nsslcacert={PATH_CA}\n"
    )


def disabled_apt_sources() -> dict[str, str]:
    """Empty out the stock apt sources inside the config image, so builds cannot reach the internet mirrors."""
    note = "# Disabled by Outpost. Packages come from Gitea, see outpost.list\n"
    return {
        "/etc/apt/sources.list": note,
        "/etc/apt/sources.list.d/debian.sources": note,
        "/etc/apt/sources.list.d/ubuntu.sources": note,
    }


def disabled_os_sources(apk_branches: list[str] | None = None) -> dict[str, str]:
    """The apk and dnf counterpart of disabled_apt_sources, for the config image.

    The image is one layer copied over the base, so a stock file cannot be moved aside, only overwritten:
    every well-known RHEL-family repo file becomes a comment-only file, and /etc/apk/repositories is
    blanked, unless Gitea has Alpine branches, in which case client_files writes the Gitea lines there.
    """
    note = "# Disabled by Outpost. Packages come from Gitea, see outpost.repo\n"
    files = {f"/etc/yum.repos.d/{name}": note for name in STOCK_RPM_REPOS}
    if not apk_branches:
        files[PATH_APK_REPOS] = "# Disabled by Outpost. No Alpine packages were uploaded to Gitea.\n"
    return files
