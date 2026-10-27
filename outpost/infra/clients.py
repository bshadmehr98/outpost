"""The small config files that make pip, npm, apt, apk, dnf and Docker talk to Gitea instead of the internet.

The same files go to every node and into the `config` image that Dockerfiles copy from,
so a build container and the host see exactly the same world.

Everything refers to Gitea by name. Hosts resolve it through /etc/hosts; containers cannot see
that file, so each node runs a tiny DNS server (CoreDNS) that answers from /etc/hosts, and
Docker and k3s are told to hand that server to every container.

When servers.yml has an egress_proxy, the same files also tell programs where the proxy is, through
the de facto standard HTTPS_PROXY, HTTP_PROXY and NO_PROXY variables (upper and lower case, because
curl reads only `http_proxy` in lower case and busybox only the lower-case names):

  /etc/outpost/proxy.env        KEY=value lines, for `env_file:` in Compose and Swarm files and for scripts
  /etc/profile.d/outpost.sh     the same as exports, for interactive shells
  /etc/environment              the same, for PAM sessions (ssh logins)
  ~/.docker/config.json         `proxies.default` (httpProxy, httpsProxy, noProxy): the Docker CLI puts
                                these into every container it creates with `docker run` / `docker create`
                                and into every `docker build` as the predefined proxy build arguments,
                                https://docs.docker.com/engine/cli/proxy/ ("Configure the Docker client").
                                Written for root and for the Actions runner's HOME, so jobs get it too.

Nothing is configured on the Docker daemon (no `Environment=HTTPS_PROXY` drop-in on docker.service and
no `proxies` in daemon.json, https://docs.docker.com/engine/daemon/proxy/): the daemon pulls images,
and image pulls must keep going to Gitea only, so a bare Docker Hub pull still fails at the firewall.
`docker stack deploy` creates services, not containers, and does not apply the client's proxies; a
Swarm service opts in with `env_file: /etc/outpost/proxy.env`. k3s gets a ConfigMap instead (k3s.py).
"""

import json
import shlex
from dataclasses import dataclass, field
from urllib.parse import quote

PATH_CA = "/etc/outpost/ca.crt"
PATH_APT_KEY = "/etc/outpost/gitea-apt.asc"
PATH_RPM_KEY = "/etc/outpost/gitea-rpm.asc"
PATH_APK_KEYS = "/etc/apk/keys"            # apk only reads keys from here, under the name the signer used
PATH_APK_REPOS = "/etc/apk/repositories"
PATH_RPM_REPO = "/etc/yum.repos.d/outpost.repo"
PATH_PROXY_ENV = "/etc/outpost/proxy.env"
PATH_PROFILE = "/etc/profile.d/outpost.sh"
PATH_ENVIRONMENT = "/etc/environment"
PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
NO_PROXY_ENV = "# No egress proxy is configured (servers.yml has no egress_proxy). Kept so env_file: can name it.\n"

# The repo files the official RHEL-family images ship. A single-layer config image cannot move them
# aside, so each is overwritten with a comment-only file; a name not listed here is still harmless on
# a sealed node (the seal rejects the mirror), it just fails later and louder.
STOCK_RPM_REPOS = (
    "rocky.repo", "rocky-extras.repo", "rocky-addons.repo", "rocky-devel.repo",
    "almalinux-baseos.repo", "almalinux-appstream.repo", "almalinux-crb.repo", "almalinux-extras.repo",
    "almalinux-highavailability.repo", "almalinux-nfv.repo", "almalinux-plus.repo", "almalinux-resilientstorage.repo",
    "almalinux-rt.repo", "almalinux-sap.repo", "almalinux-saphana.repo",
    "fedora.repo", "fedora-updates.repo", "fedora-updates-testing.repo", "fedora-cisco-openh264.repo",
    "centos.repo", "centos-addons.repo",
)


@dataclass(frozen=True)
class GiteaInfo:
    name: str   # DNS name, e.g. gitea.internal
    ip: str
    org: str    # organisation holding pip, npm, apt, apk, rpm and files

    @property
    def url(self) -> str:
        return f"https://{self.name}"

    @property
    def pypi_index(self) -> str:
        return f"{self.url}/api/packages/{self.org}/pypi/simple"

    @property
    def npm_registry(self) -> str:
        return f"{self.url}/api/packages/{self.org}/npm/"

    @property
    def apt_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/debian"

    @property
    def apk_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/alpine"

    @property
    def rpm_repo(self) -> str:
        return f"{self.url}/api/packages/{self.org}/rpm"

    def generic(self, name: str, version: str, filename: str) -> str:
        return f"{self.url}/api/packages/{self.org}/generic/{quote(name, safe='')}/{quote(version, safe='')}/{quote(filename, safe='')}"

    @property
    def config_image(self) -> str:
        return f"{self.name}/{self.org}/config:latest"


@dataclass(frozen=True)
class ProxyInfo:
    """Where the egress proxy is, as the nodes see it. Built by infra/proxy.py from servers.yml."""
    url: str                 # http://gitea.internal:3128, or http://proxy.internal:3128 on another host
    no_proxy: str            # what must never go through the proxy: Gitea, loopback, private networks
    ip: str                  # the address the proxy listens on
    name: str                # the name nodes use for it (the Gitea name when it runs on the Gitea host)
    allowed: tuple[str, ...] = field(default_factory=tuple)   # servers.yml egress_proxy.allow
    host: str = ""           # SSH alias of the proxy host
    on_gitea: bool = True    # runs on the Gitea host, so nodes need no new address or name

    def env(self) -> dict[str, str]:
        """The variables, in the order they are written everywhere."""
        return {"HTTPS_PROXY": self.url, "HTTP_PROXY": self.url, "NO_PROXY": self.no_proxy,
                "https_proxy": self.url, "http_proxy": self.url, "no_proxy": self.no_proxy}


def proxy_env(proxy: ProxyInfo) -> str:
    """/etc/outpost/proxy.env: KEY=value lines for env_file: and `set -a; . file`. OUTPOST_PROXY_ALLOWED is
    informational (the demo app probes those names); the proxy enforces the list, not the client."""
    lines = [f"{k}={v}" for k, v in proxy.env().items()] + [f"OUTPOST_PROXY_ALLOWED={','.join(proxy.allowed)}"]
    return ("# Written by Outpost: the egress proxy. Programs that honour these variables reach the names in\n"
            "# OUTPOST_PROXY_ALLOWED through it; everything else still has no internet.\n" + "\n".join(lines) + "\n")


def proxy_exports(proxy: ProxyInfo) -> str:
    return "".join(f"export {k}={v}\n" for k, v in proxy.env().items())


def docker_client_config(existing: str, proxy: ProxyInfo | None, gitea_name: str) -> str | None:
    """~/.docker/config.json with `proxies.default` set (or removed), everything else (auths from
    `docker login`) kept. None when nothing has to change or the file is not JSON we can safely edit.
    Without a proxy, only a `default` entry Outpost wrote is removed: its noProxy starts with the Gitea name."""
    try:
        data = json.loads(existing) if existing.strip() else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    proxies = data.get("proxies") if isinstance(data.get("proxies"), dict) else {}
    if proxy:
        want = {"httpProxy": proxy.url, "httpsProxy": proxy.url, "noProxy": proxy.no_proxy}
        if proxies.get("default") == want:
            return None
        proxies["default"] = want
        data["proxies"] = proxies
    else:
        mine = proxies.get("default") or {}
        if not str(mine.get("noProxy", "")).startswith(gitea_name + ","):
            return None
        del proxies["default"]
        if not proxies:
            data.pop("proxies", None)
    return json.dumps(data, indent=2) + "\n"


def environment_script(proxy: ProxyInfo | None) -> str:
    """Shell that puts the proxy lines into /etc/environment (pam_env reads KEY=value, no `export`) after
    removing earlier ones, rewriting the file in place. Without a proxy it only cleans up what Outpost wrote."""
    marker = "# Outpost egress proxy"
    strip = f"grep -vE '^({marker}|HTTPS?_PROXY=|https?_proxy=|NO_PROXY=|no_proxy=)' {PATH_ENVIRONMENT} || true"
    if proxy:
        lines = "".join(f"echo {shlex.quote(f'{k}={v}')}; " for k, v in proxy.env().items())
        return (f"touch {PATH_ENVIRONMENT}\n"
                f"{{ {strip}; echo '{marker}'; {lines}}} > {PATH_ENVIRONMENT}.outpost && cat {PATH_ENVIRONMENT}.outpost > {PATH_ENVIRONMENT} && rm -f {PATH_ENVIRONMENT}.outpost\n")
    return (f"if grep -qs '^{marker}' {PATH_ENVIRONMENT}; then {{ {strip}; }} > {PATH_ENVIRONMENT}.outpost && "
            f"cat {PATH_ENVIRONMENT}.outpost > {PATH_ENVIRONMENT} && rm -f {PATH_ENVIRONMENT}.outpost; fi\n")


def apt_source_line(gitea: GiteaInfo, distro: str) -> str:
    return f"deb [signed-by={PATH_APT_KEY}] {gitea.apt_repo} {distro} main"


def apk_repository_line(gitea: GiteaInfo, branch: str, repository: str = "main") -> str:
    return f"{gitea.apk_repo}/{branch}/{repository}"


def rpm_repo_section(gitea: GiteaInfo, distro: str) -> str:
    """One [section] of outpost.repo: Gitea serves one RPM repository per group, and the group is the distro.
    gpgcheck stays on: Gitea signs the repository metadata and the packages it serves with the key at gpgkey."""
    return (f"[outpost-{distro}]\nname=Outpost {distro} (Gitea)\nbaseurl={gitea.rpm_repo}/{distro}\nenabled=1\n"
            f"gpgcheck=1\nrepo_gpgcheck=0\ngpgkey=file://{PATH_RPM_KEY}\nsslcacert={PATH_CA}\n")


def client_files(gitea: GiteaInfo, ca_pem: str, apt_key: str | None, distros: list[str],
                 apk_key=None, apk_branches: list[str] | None = None,
                 rpm_key: str | None = None, rpm_distros: list[str] | None = None,
                 proxy: ProxyInfo | None = None) -> dict[str, str]:
    """path -> content. Shared by nodes and the config image. `apk_key` is an AlpineKey (name, content):
    the file under /etc/apk/keys must carry the name Gitea signed with, and it is only written when a
    branch exists, because apk rejects an index whose key it does not have but accepts a missing key dir.
    With a `proxy`, /etc/outpost/proxy.env is added; without one, nothing about a proxy is written."""
    npmrc = f"registry={gitea.npm_registry}\ncafile={PATH_CA}\n"
    files = {
        PATH_CA: ca_pem,
        "/etc/pip.conf": f"[global]\nindex-url = {gitea.pypi_index}\ncert = {PATH_CA}\n",
        "/etc/npmrc": npmrc,
        "/usr/local/etc/npmrc": npmrc,  # npm reads the file under its prefix; node images use /usr/local
        "/etc/apt/apt.conf.d/99outpost": f'Acquire::https::CaInfo "{PATH_CA}";\n',
    }
    if apt_key:
        files[PATH_APT_KEY] = apt_key
    if distros:
        files["/etc/apt/sources.list.d/outpost.list"] = "".join(apt_source_line(gitea, d) + "\n" for d in distros)
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


def disabled_apt_sources() -> dict[str, str]:
    """Empty out the stock apt sources inside the config image so builds cannot reach the internet mirrors."""
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
    blanked, unless Gitea has Alpine branches, in which case client_files writes the Gitea lines there."""
    note = "# Disabled by Outpost. Packages come from Gitea, see outpost.repo\n"
    files = {f"/etc/yum.repos.d/{name}": note for name in STOCK_RPM_REPOS}
    if not apk_branches:
        files[PATH_APK_REPOS] = "# Disabled by Outpost. No Alpine packages were uploaded to Gitea.\n"
    return files


def corefile(node_ip: str) -> str:
    """CoreDNS config: answer names from /etc/hosts, forward the rest to the host's usual resolvers."""
    return (
        f".:53 {{\n"
        f"    bind {node_ip}\n"
        "    hosts /etc/hosts {\n        fallthrough\n    }\n"
        "    forward . /etc/resolv.conf\n"
        "    cache 30\n"
        "    reload\n"
        "    errors\n"
        "}\n"
    )


def profile_script(gitea: GiteaInfo, proxy: ProxyInfo | None = None) -> str:
    return (f"export PIP_INDEX_URL={gitea.pypi_index}\nexport PIP_CERT={PATH_CA}\n"
            f"export NPM_CONFIG_REGISTRY={gitea.npm_registry}\nexport NPM_CONFIG_CAFILE={PATH_CA}\n"
            + (proxy_exports(proxy) if proxy else ""))


def host_files(gitea: GiteaInfo, ca_pem: str, node_ip: str, proxy: ProxyInfo | None = None) -> dict[str, str]:
    """Extra files that only make sense on a real host. With a proxy, profile.d also exports its variables
    (~/.docker/config.json and /etc/environment are merged on the host, see common.apply_proxy)."""
    return {
        # no registry-mirrors: images are referenced by their Gitea name, so a bare Docker Hub name fails at once
        "/etc/docker/daemon.json": json.dumps({"dns": [node_ip]}, indent=2) + "\n",
        f"/etc/docker/certs.d/{gitea.name}/ca.crt": ca_pem,
        "/etc/outpost/Corefile": corefile(node_ip),
        "/etc/outpost/resolv.conf": f"nameserver {node_ip}\n",  # handed to k3s so pods use the node's DNS
        PATH_PROFILE: profile_script(gitea, proxy),
    }


def config_image_files(gitea: GiteaInfo, ca_pem: str, apt_key: str | None, distros: list[str],
                       apk_key=None, apk_branches: list[str] | None = None,
                       rpm_key: str | None = None, rpm_distros: list[str] | None = None,
                       proxy: ProxyInfo | None = None) -> dict[str, tuple[bytes, int]]:
    """Text files only: the config image is copied onto any base, so it must not carry a distro's binaries.
    With a proxy it carries /etc/outpost/proxy.env and the profile.d exports, for interactive shells and
    env_file; RUN steps get the variables from the Docker client config on the node, not from these files."""
    merged = {**disabled_apt_sources(), **disabled_os_sources(apk_branches),
              **client_files(gitea, ca_pem, apt_key, distros, apk_key, apk_branches, rpm_key, rpm_distros, proxy=proxy)}
    if proxy:
        merged[PATH_PROFILE] = proxy_exports(proxy)
    return {path: (content.encode(), 0o644) for path, content in merged.items()}
