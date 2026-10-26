"""The small config files that make pip, npm, apt and Docker talk to Gitea instead of the internet.

The same files go to every node and into the `config` image that Dockerfiles copy from,
so a build container and the host see exactly the same world.

Everything refers to Gitea by name. Hosts resolve it through /etc/hosts; containers cannot see
that file, so each node runs a tiny DNS server (CoreDNS) that answers from /etc/hosts, and
Docker and k3s are told to hand that server to every container.
"""

import json
from dataclasses import dataclass
from urllib.parse import quote

PATH_CA = "/etc/outpost/ca.crt"
PATH_APT_KEY = "/etc/outpost/gitea-apt.asc"


@dataclass(frozen=True)
class GiteaInfo:
    name: str   # DNS name, e.g. gitea.internal
    ip: str
    org: str    # organisation holding pip, npm, apt and files

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

    def generic(self, name: str, version: str, filename: str) -> str:
        return f"{self.url}/api/packages/{self.org}/generic/{quote(name, safe='')}/{quote(version, safe='')}/{quote(filename, safe='')}"

    @property
    def config_image(self) -> str:
        return f"{self.name}/{self.org}/config:latest"


def apt_source_line(gitea: GiteaInfo, distro: str) -> str:
    return f"deb [signed-by={PATH_APT_KEY}] {gitea.apt_repo} {distro} main"


def client_files(gitea: GiteaInfo, ca_pem: str, apt_key: str | None, distros: list[str]) -> dict[str, str]:
    """path -> content. Shared by nodes and the config image."""
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
    return files


def disabled_apt_sources() -> dict[str, str]:
    """Empty out the stock apt sources inside the config image so builds cannot reach the internet mirrors."""
    note = "# Disabled by Outpost. Packages come from Gitea, see outpost.list\n"
    return {
        "/etc/apt/sources.list": note,
        "/etc/apt/sources.list.d/debian.sources": note,
        "/etc/apt/sources.list.d/ubuntu.sources": note,
    }


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


def host_files(gitea: GiteaInfo, ca_pem: str, node_ip: str) -> dict[str, str]:
    """Extra files that only make sense on a real host."""
    return {
        "/etc/docker/daemon.json": json.dumps({"registry-mirrors": [gitea.url], "dns": [node_ip]}, indent=2) + "\n",
        f"/etc/docker/certs.d/{gitea.name}/ca.crt": ca_pem,
        "/etc/outpost/Corefile": corefile(node_ip),
        "/etc/outpost/resolv.conf": f"nameserver {node_ip}\n",  # handed to k3s so pods use the node's DNS
        "/etc/profile.d/outpost.sh": (
            f"export PIP_INDEX_URL={gitea.pypi_index}\nexport PIP_CERT={PATH_CA}\n"
            f"export NPM_CONFIG_REGISTRY={gitea.npm_registry}\nexport NPM_CONFIG_CAFILE={PATH_CA}\n"
        ),
    }


def config_image_files(gitea: GiteaInfo, ca_pem: str, apt_key: str | None, distros: list[str]) -> dict[str, tuple[bytes, int]]:
    merged = {**client_files(gitea, ca_pem, apt_key, distros), **disabled_apt_sources()}
    return {path: (content.encode(), 0o644) for path, content in merged.items()}
