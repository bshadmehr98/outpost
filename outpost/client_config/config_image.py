"""The files of the `config` image, which Dockerfiles copy from: COPY --from=<gitea>/<org>/config / /"""

from typing import TYPE_CHECKING

from outpost.client_config.package_sources import client_files, disabled_apt_sources, disabled_os_sources
from outpost.client_config.paths import PATH_CA_DIR, PATH_PROFILE
from outpost.client_config.proxy_settings import proxy_exports

if TYPE_CHECKING:  # only for the type hints: endpoints imports PRIVATE_NETS from this package
    from outpost.endpoints import GiteaEndpoint, ProxyEndpoint


def config_image_files(
    gitea: "GiteaEndpoint",
    ca_pem: str,
    apt_key: str | None,
    distros: list[str],
    apk_key=None,
    apk_branches: list[str] | None = None,
    rpm_key: str | None = None,
    rpm_distros: list[str] | None = None,
    proxy: "ProxyEndpoint | None" = None,
    ca_hash: str | None = None,
) -> dict[str, tuple[bytes, int]]:
    """The image's files, as path -> (content, mode). Text files only: the config image is copied onto
    any base, so it must not carry a distro's binaries.

    The stock package sources are blanked, and the client files (package_sources.client_files) go on top.

    Args:
        ca_hash: the CA's OpenSSL subject hash (pki.subject_hash). The CA is then also written as
            /etc/ssl/certs/<hash>.0, where OpenSSL's default lookup finds it. apk has no CA option of its
            own and an Alpine base has no update-ca-certificates, so this is how `apk add` over https
            trusts Gitea in a build.
        proxy: the egress proxy. The image then carries /etc/outpost/proxy.env and the profile.d exports,
            for interactive shells and env_file; RUN steps get the variables from the Docker client config
            on the node, not from these files.
        The others: as for client_files.
    """
    merged = {
        **disabled_apt_sources(),
        **disabled_os_sources(apk_branches),
        **client_files(gitea, ca_pem, apt_key, distros, apk_key, apk_branches, rpm_key, rpm_distros, proxy=proxy),
    }
    if proxy:
        merged[PATH_PROFILE] = proxy_exports(proxy)
    if ca_hash:
        merged[f"{PATH_CA_DIR}/{ca_hash}.0"] = ca_pem
    return {path: (content.encode(), 0o644) for path, content in merged.items()}
