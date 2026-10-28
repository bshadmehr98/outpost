"""The small config files that make pip, npm, apt, apk, dnf and Docker talk to Gitea instead of the internet.

    files = client_files(gitea, ca_pem, apt_key, distros, proxy=proxy)   # path -> content, for every node
    files |= node_files(gitea, ca_pem, node_ip, proxy=proxy)             # and the ones only a node gets
    image = config_image_files(gitea, ca_pem, ...)                       # path -> (bytes, mode), the config image

The same files go to every node and into the `config` image that Dockerfiles copy from, so a build
container and the node see exactly the same world. These functions only build text; infra/node writes
them to the nodes, and gitea/upload.py builds the image from them.

Everything refers to Gitea by name. Nodes resolve it through /etc/hosts; containers cannot see that
file, so each node runs a tiny DNS server (CoreDNS) that answers from /etc/hosts, and Docker and k3s
are told to hand that server to every container.

When servers.yml has an egress_proxy, the same files also tell programs where the proxy is (see
proxy_settings.py). Nothing is configured on the Docker daemon (no `Environment=HTTPS_PROXY` drop-in on
docker.service and no `proxies` in daemon.json, https://docs.docker.com/engine/daemon/proxy/): the
daemon pulls images, and image pulls must keep going to Gitea only, so a bare Docker Hub pull still
fails at the firewall. `docker stack deploy` creates services, not containers, and does not apply the
client's proxies; a Swarm service opts in with `env_file: /etc/outpost/proxy.env`. k3s gets a ConfigMap
instead (infra/k3s.py).

    paths.py            PATH_* constants, PRIVATE_NETS, RUNNER_HOME, STOCK_RPM_REPOS
    package_sources.py  client_files(), apt_source_line(), apk_repository_line(), rpm_repo_section(),
                        disabled_apt_sources(), disabled_os_sources()
    proxy_settings.py   proxy_env(), proxy_exports(), docker_client_config(), environment_script(), NO_PROXY_ENV
    node_files.py       node_files(), corefile(), profile_script()
    config_image.py     config_image_files()

Code outside this folder uses only the names below.
"""

from outpost.client_config.config_image import config_image_files
from outpost.client_config.node_files import corefile, node_files, profile_script
from outpost.client_config.package_sources import (
    apk_repository_line,
    apt_source_line,
    apt_trusts_the_ca,
    client_files,
    disabled_apt_sources,
    disabled_os_sources,
    rpm_repo_section,
)
from outpost.client_config.paths import (
    PATH_APK_KEYS,
    PATH_APK_REPOS,
    PATH_APT_KEY,
    PATH_APT_LIST,
    PATH_CA,
    PATH_CA_DIR,
    PATH_COREFILE,
    PATH_ENVIRONMENT,
    PATH_NODE_RESOLV_CONF,
    PATH_PROFILE,
    PATH_PROXY_ENV,
    PATH_RPM_KEY,
    PATH_RPM_REPO,
    PRIVATE_NETS,
    RUNNER_HOME,
    STOCK_RPM_REPOS,
)
from outpost.client_config.proxy_settings import (
    NO_PROXY_ENV,
    docker_client_config,
    environment_script,
    proxy_env,
    proxy_exports,
)

__all__ = [
    "NO_PROXY_ENV",
    "PATH_APK_KEYS",
    "PATH_APK_REPOS",
    "PATH_APT_KEY",
    "PATH_APT_LIST",
    "PATH_CA",
    "PATH_CA_DIR",
    "PATH_COREFILE",
    "PATH_ENVIRONMENT",
    "PATH_NODE_RESOLV_CONF",
    "PATH_PROFILE",
    "PATH_PROXY_ENV",
    "PATH_RPM_KEY",
    "PATH_RPM_REPO",
    "PRIVATE_NETS",
    "RUNNER_HOME",
    "STOCK_RPM_REPOS",
    "apk_repository_line",
    "apt_source_line",
    "apt_trusts_the_ca",
    "client_files",
    "config_image_files",
    "corefile",
    "disabled_apt_sources",
    "disabled_os_sources",
    "docker_client_config",
    "environment_script",
    "node_files",
    "profile_script",
    "proxy_env",
    "proxy_exports",
    "rpm_repo_section",
]
