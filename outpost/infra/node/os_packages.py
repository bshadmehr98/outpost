"""Step 4 of a node: OS packages from Gitea, for the node's family (apt, apk or dnf).

Each family does the same three things: fetch Gitea's signing key, park the stock sources (moved
aside, not deleted), and write a source for each distro or branch Gitea actually serves. A probe
asks Gitea first, so a node never gets a source that would fail every `apt-get update`.
"""

import shlex

from outpost import client_config, versions
from outpost.client_config import (
    PATH_APK_KEYS,
    PATH_APK_REPOS,
    PATH_APT_KEY,
    PATH_APT_LIST,
    PATH_CA,
    PATH_RPM_KEY,
    PATH_RPM_REPO,
)
from outpost.distros import apk_branch_of, os_family, rpm_distro_of_host
from outpost.infra.context import NodeContext
from outpost.infra.node.download import download_from_gitea
from outpost.ssh import Host


def configure_os_packages(host: Host, ctx: NodeContext, facts: dict, log=print) -> None:
    """Point the node's package manager at Gitea, by the family its facts say."""
    family = os_family(facts)
    log(f"  [{host.alias}] os packages: {family}")
    CONFIGURE_BY_FAMILY[family](host, ctx, facts)


def configure_apt_sources(host: Host, ctx: NodeContext, facts: dict) -> None:
    """Debian and Ubuntu: one line in outpost.list per suite Gitea serves.

    The probe is a GET: Gitea routes the Debian registry's dists/ files for GET only, so a HEAD (curl -I)
    gets 405 and every suite looked missing (Gitea's Alpine and RPM registries do answer HEAD).
    """
    g = ctx.gitea
    distros = list(dict.fromkeys(ctx.distros + ([facts["codename"]] if facts.get("codename") else [])))
    print_line_if_gitea_has_it = "\n".join(
        f"if curl --cacert {PATH_CA} -fsS -o /dev/null --max-time 10 {g.apt_repo}/dists/{d}/Release >/dev/null 2>&1; "
        f"then echo {shlex.quote(client_config.apt_source_line(g, d))}; fi"
        for d in distros
    )
    host.run(
        _lines(
            f"{download_from_gitea(g.apt_repo + '/repository.key', PATH_APT_KEY)} || true",
            "mkdir -p /etc/apt/sources.disabled /etc/apt/sources.list.d",
            'for f in /etc/apt/sources.list.d/*; do [ -e "$f" ] && [ "$(basename $f)" != outpost.list ] && mv "$f" /etc/apt/sources.disabled/; done; true',
            "if [ -s /etc/apt/sources.list ]; then mv /etc/apt/sources.list /etc/apt/sources.disabled/sources.list; fi",
            f"{{ {print_line_if_gitea_has_it}\n}} > {PATH_APT_LIST}",
        ),
        raise_on_failure=True,
    )


def configure_apk_sources(host: Host, ctx: NodeContext, facts: dict) -> None:
    """Alpine: only the branches Gitea serves, in /etc/apk/repositories.

    The key goes to /etc/apk/keys under the name Gitea sends (curl -J keeps it), and the stock
    /etc/apk/repositories is parked as repositories.disabled.
    """
    g = ctx.gitea
    arch = versions.arch_names(ctx.arch)["uname"]
    own = apk_branch_of(facts)
    branches = list(dict.fromkeys(ctx.apk_branches + ([own] if own else [])))
    print_line_if_gitea_has_it = "\n".join(
        f"if curl --cacert {PATH_CA} -fsI --max-time 10 {g.apk_repo}/{b}/main/{arch}/APKINDEX.tar.gz >/dev/null 2>&1; "
        f"then echo {shlex.quote(client_config.apk_repository_line(g, b))}; fi"
        for b in branches
    )
    # -J saves under the name in Content-Disposition, the one Gitea signed with; `key` means the header was missing
    fetch_key_under_gitea_name = (
        f"(cd {PATH_APK_KEYS} && curl --cacert {PATH_CA} -fsSL -J -O {shlex.quote(g.apk_repo + '/key')} && "
        f"{{ [ ! -f key ] || mv key {shlex.quote(g.org + '.rsa.pub')}; }}) || true"
    )
    host.run(
        _lines(
            f"mkdir -p {PATH_APK_KEYS}",
            f"find {PATH_APK_KEYS} -name {shlex.quote(g.org + '@*.rsa.pub')} -delete",
            fetch_key_under_gitea_name,
            f"if [ -e {PATH_APK_REPOS} ] && ! grep -q {shlex.quote(g.apk_repo)} {PATH_APK_REPOS}; "
            f"then mv {PATH_APK_REPOS} {PATH_APK_REPOS}.disabled; fi",
            f"{{ {print_line_if_gitea_has_it}\n}} > {PATH_APK_REPOS}",
        ),
        raise_on_failure=True,
    )


def configure_rpm_sources(host: Host, ctx: NodeContext, facts: dict) -> None:
    """RHEL family: outpost.repo with one section per distro (Gitea group) that exists.

    Gitea's key goes to /etc/outpost/gitea-rpm.asc, and the stock *.repo files are parked in
    /etc/yum.repos.d.disabled/.
    """
    g = ctx.gitea
    own = rpm_distro_of_host(facts)
    distros = list(dict.fromkeys(ctx.rpm_distros + ([own] if own else [])))
    print_section_if_gitea_has_it = "\n".join(
        f"if curl --cacert {PATH_CA} -fsI --max-time 10 {g.rpm_repo}/{d}/repodata/repomd.xml >/dev/null 2>&1; "
        f"then printf '%s\\n' {shlex.quote(client_config.rpm_repo_section(g, d))}; fi"
        for d in distros
    )
    host.run(
        _lines(
            f"{download_from_gitea(g.rpm_repo + '/repository.key', PATH_RPM_KEY)} || true",
            "mkdir -p /etc/yum.repos.d.disabled /etc/yum.repos.d",
            'for f in /etc/yum.repos.d/*.repo; do [ -e "$f" ] && [ "$(basename $f)" != outpost.repo ] && mv "$f" /etc/yum.repos.d.disabled/; done; true',
            f"{{ {print_section_if_gitea_has_it}\n}} > {PATH_RPM_REPO}",
        ),
        raise_on_failure=True,
    )


CONFIGURE_BY_FAMILY = {"apt": configure_apt_sources, "apk": configure_apk_sources, "rpm": configure_rpm_sources}


def _lines(*lines: str) -> str:
    """A script of one command per line."""
    return "".join(line + "\n" for line in lines)
