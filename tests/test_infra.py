import json

from outpost import versions
from outpost.gitea.client import AlpineKey
from outpost.infra import clients, common, compose, egress, k3s, swarm, verify
from outpost.infra.clients import GiteaInfo
from outpost.infra.common import NodeContext
from tests.conftest import FakeHost

G = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
CTX = NodeContext(gitea=G, ca_pem="-----BEGIN CERTIFICATE-----\nca\n", distros=["bookworm"], allow=["203.0.113.9"], infra_name="dev")


def test_client_files_point_everything_at_gitea():
    files = clients.client_files(G, "CA", "KEY", ["bookworm", "noble"])
    assert files["/etc/pip.conf"] == "[global]\nindex-url = https://gitea.internal/api/packages/pkgs/pypi/simple\ncert = /etc/outpost/ca.crt\n"
    assert files["/etc/npmrc"] == files["/usr/local/etc/npmrc"] == "registry=https://gitea.internal/api/packages/pkgs/npm/\ncafile=/etc/outpost/ca.crt\n"
    assert files["/etc/apt/sources.list.d/outpost.list"].splitlines() == [
        "deb [signed-by=/etc/outpost/gitea-apt.asc] https://gitea.internal/api/packages/pkgs/debian bookworm main",
        "deb [signed-by=/etc/outpost/gitea-apt.asc] https://gitea.internal/api/packages/pkgs/debian noble main",
    ]
    assert files["/etc/outpost/gitea-apt.asc"] == "KEY"
    image = clients.config_image_files(G, "CA", "KEY", ["bookworm"])
    assert image["/etc/apt/sources.list.d/debian.sources"][0].startswith(b"# Disabled by Outpost")
    host = clients.host_files(G, "CA", "10.0.0.11")
    daemon = json.loads(host["/etc/docker/daemon.json"])
    assert daemon == {"dns": ["10.0.0.11"]} and "registry-mirrors" not in daemon   # images are named by their Gitea copy, nothing is rewritten
    assert host["/etc/docker/certs.d/gitea.internal/ca.crt"] == "CA"
    registries = k3s.registries_yaml(CTX)
    assert "docker.io" not in registries and "ca_file: /etc/outpost/ca.crt" in registries and 'endpoint:\n      - "https://gitea.internal"' in registries
    for module in (compose, swarm):
        checks = dict(module.extra_checks(CTX))
        assert not any("mirror" in name or "mirror" in cmd for name, cmd in checks.items())
        assert "test -s /etc/docker/certs.d/gitea.internal/ca.crt" in checks["docker trusts and reaches gitea registry"]
        assert "https://gitea.internal/v2/" in checks["docker trusts and reaches gitea registry"]
    assert "bind 10.0.0.11" in host["/etc/outpost/Corefile"] and "hosts /etc/hosts" in host["/etc/outpost/Corefile"]
    assert host["/etc/outpost/resolv.conf"] == "nameserver 10.0.0.11\n"
    assert G.config_image == "gitea.internal/pkgs/config:latest"
    assert G.generic("docker", "1", "d.tgz") == "https://gitea.internal/api/packages/pkgs/generic/docker/1/d.tgz"


APK_KEY = AlpineKey(name="pkgs@6f2a9c1e.rsa.pub", content="RSA KEY")


def test_client_files_for_apk_and_rpm_point_at_gitea_only():
    files = clients.client_files(G, "CA", None, [], apk_key=APK_KEY, apk_branches=["v3.20", "v3.19"], rpm_key="RPM KEY", rpm_distros=["rocky9", "fedora40"])
    assert files["/etc/apk/repositories"] == "https://gitea.internal/api/packages/pkgs/alpine/v3.20/main\nhttps://gitea.internal/api/packages/pkgs/alpine/v3.19/main\n"
    assert files["/etc/apk/keys/pkgs@6f2a9c1e.rsa.pub"] == "RSA KEY"   # the name Gitea signs with, or apk ignores the index
    repo = files["/etc/yum.repos.d/outpost.repo"]
    assert repo.startswith("[outpost-rocky9]\n") and "[outpost-fedora40]\n" in repo
    assert "baseurl=https://gitea.internal/api/packages/pkgs/rpm/rocky9\n" in repo and "baseurl=https://gitea.internal/api/packages/pkgs/rpm/fedora40\n" in repo
    assert repo.count("gpgkey=file:///etc/outpost/gitea-rpm.asc\n") == 2 and repo.count("sslcacert=/etc/outpost/ca.crt\n") == 2 and "gpgcheck=1" in repo
    assert files["/etc/outpost/gitea-rpm.asc"] == "RPM KEY"
    assert "/etc/apt/sources.list.d/outpost.list" not in files and "/etc/outpost/gitea-apt.asc" not in files
    # without branches or distros nothing of theirs is written, in particular no key under /etc/apk/keys
    none = clients.client_files(G, "CA", "KEY", ["bookworm"], apk_key=APK_KEY, rpm_key="RPM KEY")
    assert not any(p.startswith(("/etc/apk/", "/etc/yum.repos.d/")) or p == clients.PATH_RPM_KEY for p in none)
    assert G.apk_repo == "https://gitea.internal/api/packages/pkgs/alpine" and G.rpm_repo == "https://gitea.internal/api/packages/pkgs/rpm"


def test_config_image_blanks_stock_os_sources_and_stays_text_only():
    image = clients.config_image_files(G, "CA", "KEY", ["bookworm"], apk_key=APK_KEY, apk_branches=["v3.20"], rpm_key="RPM KEY", rpm_distros=["rocky9"])
    assert image["/etc/apk/repositories"][0] == b"https://gitea.internal/api/packages/pkgs/alpine/v3.20/main\n"   # Gitea lines, not blanked
    assert image["/etc/apk/keys/pkgs@6f2a9c1e.rsa.pub"][0] == b"RSA KEY"
    for stock in ("rocky.repo", "rocky-extras.repo", "almalinux-baseos.repo", "almalinux-appstream.repo", "fedora.repo", "fedora-updates.repo", "centos.repo"):
        assert image[f"/etc/yum.repos.d/{stock}"][0].startswith(b"# Disabled by Outpost"), stock
    assert image["/etc/yum.repos.d/outpost.repo"][0].startswith(b"[outpost-rocky9]")
    assert image["/etc/apt/sources.list.d/debian.sources"][0].startswith(b"# Disabled by Outpost")
    assert all(mode == 0o644 and b"\x00" not in content for content, mode in image.values())   # text files only, no binaries
    # with no Alpine branches the stock /etc/apk/repositories is blanked and no key dir entry is made
    bare = clients.config_image_files(G, "CA", None, [])
    assert bare["/etc/apk/repositories"][0].startswith(b"# Disabled by Outpost") and not any(p.startswith("/etc/apk/keys/") for p in bare)
    assert bare["/etc/yum.repos.d/rocky.repo"][0].startswith(b"# Disabled by Outpost") and "/etc/yum.repos.d/outpost.repo" not in bare
    assert "/etc/apk/repositories" not in clients.disabled_os_sources(["v3.20"]) and "/etc/apk/repositories" in clients.disabled_os_sources()


def test_os_family_from_os_release_facts():
    assert common.os_family({"id": "ubuntu", "id_like": "debian"}) == "apt" and common.os_family({"id": "debian"}) == "apt"
    assert common.os_family({"id": "alpine"}) == "apk"
    assert common.os_family({"id": "rocky", "id_like": "rhel centos fedora"}) == "rpm" and common.os_family({"id": "fedora"}) == "rpm"
    assert common.os_family({"id": "almalinux", "id_like": "rhel centos fedora"}) == "rpm" and common.os_family({"id": "centos", "id_like": "rhel fedora"}) == "rpm"
    assert common.os_family({"codename": "noble"}) == "apt" and common.os_family({}) == "apt"   # unknown: what every node was before
    assert common.apk_branch_of({"version_id": "3.20.3"}) == "v3.20" and common.apk_branch_of({"version_id": ""}) is None


def test_prepare_node_on_alpine_configures_apk_and_parks_the_stock_repositories():
    ctx = NodeContext(gitea=G, ca_pem="CA", distros=["bookworm"], apk_branches=["v3.19"], infra_name="dev")
    host = FakeHost("alp-1", facts={"id": "alpine", "id_like": "", "version_id": "3.20.3", "codename": "", "arch": "x86_64", "ip": "10.0.0.41"},
                    responses=[(r"api/v1/version", "ok")])
    common.prepare_node(host, ctx, log=lambda *_: None)
    joined = "\n".join(host.scripts)
    step = next(s for s in host.scripts if "/etc/apk/keys" in s)
    assert "find /etc/apk/keys -name 'pkgs@*.rsa.pub' -delete" in step
    assert "cd /etc/apk/keys && curl --cacert /etc/outpost/ca.crt -fsSL -J -O https://gitea.internal/api/packages/pkgs/alpine/key" in step  # -J keeps Gitea's file name
    assert "[ ! -f key ] || mv key pkgs.rsa.pub" in step   # the documented fallback when Gitea sends no Content-Disposition
    assert "mv /etc/apk/repositories /etc/apk/repositories.disabled" in step and "> /etc/apk/repositories" in step
    assert "alpine/v3.19/main/x86_64/APKINDEX.tar.gz" in step and "alpine/v3.20/main/x86_64/APKINDEX.tar.gz" in step   # uploaded branch + the host's own
    assert "echo https://gitea.internal/api/packages/pkgs/alpine/v3.20/main" in step
    assert "sources.disabled" not in joined and "yum.repos.d" not in joined and "dists/bookworm/Release" not in joined
    assert "update-ca-certificates" in joined   # Alpine's ca-certificates package reads /usr/local/share/ca-certificates too
    assert host.files["/etc/pip.conf"].startswith("[global]") and "/etc/apk/repositories" not in host.files   # written by the step, not as a file


def test_prepare_node_on_rocky_configures_dnf_and_parks_the_stock_repo_files():
    ctx = NodeContext(gitea=G, ca_pem="CA", distros=["bookworm"], rpm_distros=["fedora40"], infra_name="dev")
    host = FakeHost("rocky-1", facts={"id": "rocky", "id_like": "rhel centos fedora", "version_id": "9.4", "codename": "", "arch": "x86_64", "ip": "10.0.0.51"},
                    responses=[(r"api/v1/version", "ok")])
    common.prepare_node(host, ctx, log=lambda *_: None)
    joined = "\n".join(host.scripts)
    step = next(s for s in host.scripts if "/etc/yum.repos.d" in s)
    assert "curl --cacert /etc/outpost/ca.crt -fsSL https://gitea.internal/api/packages/pkgs/rpm/repository.key -o /etc/outpost/gitea-rpm.asc" in step
    assert 'mkdir -p /etc/yum.repos.d.disabled' in step and '!= outpost.repo ] && mv "$f" /etc/yum.repos.d.disabled/' in step
    assert "rpm/fedora40/repodata/repomd.xml" in step and "rpm/rocky9/repodata/repomd.xml" in step   # uploaded distro + the host's own
    assert "baseurl=https://gitea.internal/api/packages/pkgs/rpm/rocky9" in step and "> /etc/yum.repos.d/outpost.repo" in step
    assert "sources.disabled" not in joined and "/etc/apk/" not in joined
    assert "update-ca-trust" in joined


def test_verify_checks_pick_the_command_by_family_on_the_node():
    checks = dict(verify.common_checks(CTX))
    assert "apt sources are gitea only" not in checks and "apt update works offline" not in checks
    sources, index = checks["package sources are gitea only"], checks["package index updates offline"]
    for cmd in (sources, index):
        assert cmd.startswith(". /etc/os-release 2>/dev/null; case \" $ID $ID_LIKE \" in *alpine*)") and "*rhel*|*fedora*|*centos*" in cmd and cmd.endswith("esac")
    assert "grep -v '^#' /etc/apk/repositories" in sources and "grep -v https://gitea.internal" in sources
    assert "grep -lsE '^enabled *= *1' /etc/yum.repos.d/*.repo | grep -v outpost.repo" in sources
    assert "grep -rhs '^deb ' /etc/apt/sources.list" in sources
    assert "apk update" in index and "dnf makecache" in index and "apt-get update -qq" in index
    script = verify.script(verify.common_checks(CTX))
    assert "CHECK|package sources are gitea only|" in script and "CHECK|package index updates offline|" in script


def test_egress_script():
    script = egress.script("10.0.0.5", ["203.0.113.9", "10.0.0.0/8"])
    assert 'GITEA_IP="10.0.0.5"' in script
    assert 'ALLOW="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 203.0.113.9"' in script
    assert "DOCKER-USER" in script and "hook $ipt filter FORWARD" in script and "hook $ipt security FORWARD" in script
    assert "REJECT" in script and "-j DROP" in script and "--dport 53" in script
    host = FakeHost()
    egress.install(host, "10.0.0.5", [])
    assert host.files[egress.SCRIPT_PATH].startswith("#!/bin/bash") and "Before=docker.service" in host.files[egress.UNIT_PATH]
    assert host.ran("systemctl enable outpost-egress")
    egress.seal(host, "test")
    assert host.scripts[-1].endswith("outpost-egress seal test")
    assert egress.status(FakeHost(responses=[(r"status", "sealed")])) == "sealed"


def test_compose_setup_configures_node_then_installs_docker_from_gitea():
    host = FakeHost("dev-1", responses=[(r"api/v1/version", '{"version":"28.2.0"}')])
    result = compose.setup(CTX, [host])
    assert result["nodes"]["dev-1"]["ip"] == "10.0.0.11"
    assert host.files["/etc/pip.conf"].startswith("[global]")
    assert host.files["/etc/docker/certs.d/gitea.internal/ca.crt"] == CTX.ca_pem
    assert host.files["/etc/systemd/system/docker.service"].count("ExecStart=/usr/local/bin/dockerd") == 1
    joined = "\n".join(host.scripts)
    assert "10.0.0.5 gitea.internal" in joined and "sed -i" not in joined   # /etc/hosts, rewritten in place
    assert "Before=outpost-dns.service docker.service" in host.files["/etc/systemd/system/outpost-hosts.service"]  # and restored at boot
    assert "echo '10.0.0.5 gitea.internal' >> /etc/hosts" in host.files["/usr/local/sbin/outpost-hosts"] and host.ran("systemctl enable outpost-hosts")
    assert "update-ca-certificates" in joined                       # system trust
    assert "mv \"$f\" /etc/apt/sources.disabled/" in joined           # stock apt sources parked
    assert "dists/bookworm/Release" in joined and "dists/noble/Release" in joined  # host codename added
    probe = next(line for line in joined.splitlines() if "dists/noble/Release" in line)
    assert "curl " in probe and " -fsI " not in probe and "-o /dev/null" in probe   # GET: Gitea's Debian dists/ answers HEAD with 405
    assert f"generic/docker/{versions.DOCKER}/docker-{versions.DOCKER}.tgz" in joined
    assert "--cacert /etc/outpost/ca.crt" in joined
    seal_index = next(i for i, s in enumerate(host.scripts) if "outpost-egress seal" in s)
    dns_index = next(i for i, s in enumerate(host.scripts) if "coredns.tgz" in s)
    docker_index = next(i for i, s in enumerate(host.scripts) if "docker.tgz" in s)
    assert seal_index < dns_index < docker_index, "DNS and Docker are installed after the seal, proving they come from Gitea"
    assert "coredns" in host.files["/etc/systemd/system/outpost-dns.service"]


def test_compose_setup_skips_installed_docker():
    host = FakeHost(responses=[(r"api/v1/version", "ok"), (r"docker --version", f"Docker version {versions.DOCKER}, build x")])
    compose.setup(CTX, [host])
    assert not host.ran("docker.tgz")


def test_unreachable_gitea_is_a_clear_error():
    from outpost.ssh import Result
    host = FakeHost(responses=[(r"api/v1/version", Result(7, "", "connection refused"))])
    try:
        compose.setup(CTX, [host])
    except RuntimeError as e:
        assert "cannot reach https://gitea.internal" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_swarm_init_and_join():
    manager = FakeHost("p1", facts={"ip": "10.0.0.21", "codename": "noble"}, responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "inactive"), (r"join-token", "SWMTKN-1-abc")])
    worker = FakeHost("p2", facts={"ip": "10.0.0.22", "codename": "noble"}, responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "inactive")])
    result = swarm.setup(CTX, [manager, worker])
    assert result["manager"] == "p1"
    assert manager.ran("docker swarm init --advertise-addr 10.0.0.21")
    assert worker.ran("docker swarm join --token SWMTKN-1-abc 10.0.0.21:2377")
    again = FakeHost("p1", facts={"ip": "10.0.0.21"}, responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "active"), (r"join-token", "T")])
    swarm.setup(CTX, [again])
    assert not again.ran("swarm init")


def test_k3s_server_and_agent():
    server = FakeHost("k1", facts={"ip": "10.0.0.31", "codename": "noble"}, responses=[(r"api/v1/version", "ok"), (r"node-token", "K10abc::server:xyz")])
    agent = FakeHost("k2", facts={"ip": "10.0.0.32", "codename": "noble"}, responses=[(r"api/v1/version", "ok")])
    result = k3s.setup(CTX, [server, agent])
    assert result["server"] == "k1"
    assert "docker.io" not in server.files["/etc/rancher/k3s/registries.yaml"] and "ca_file: /etc/outpost/ca.crt" in server.files["/etc/rancher/k3s/registries.yaml"]
    assert server.ran("INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_EXEC='server --node-ip 10.0.0.31 --resolv-conf /etc/outpost/resolv.conf'")
    custom = NodeContext(gitea=G, ca_pem="CA", args="--disable traefik")
    s2 = FakeHost("k9", facts={"ip": "10.0.0.39"}, responses=[(r"api/v1/version", "ok"), (r"node-token", "T")])
    k3s.setup(custom, [s2])
    assert s2.ran("--resolv-conf /etc/outpost/resolv.conf --disable traefik'")
    assert server.ran("k3s-airgap-images-amd64.tar.gz")
    assert agent.ran("K3S_URL=https://10.0.0.31:6443 K3S_TOKEN=K10abc::server:xyz")
    assert agent.ran("INSTALL_K3S_EXEC='agent --resolv-conf /etc/outpost/resolv.conf'")


def test_verify_script_and_parse():
    checks = verify.common_checks(CTX) + compose.extra_checks(CTX)
    script = verify.script(checks)
    assert script.count("CHECK|") == 2 * len(checks)
    output = "CHECK|gitea in /etc/hosts|OK|\nCHECK|public internet rejected|FAIL|curl: (7) Failed\nnoise\n"
    assert verify.parse(output) == [
        {"name": "gitea in /etc/hosts", "ok": True, "detail": ""},
        {"name": "public internet rejected", "ok": False, "detail": "curl: (7) Failed"},
    ]
    host = FakeHost(responses=[(r"CHECK", "CHECK|a|OK|x\n")])
    assert verify.verify_node(host, CTX, []) == [{"name": "a", "ok": True, "detail": "x"}]
    assert verify.verify_node(FakeHost(), CTX, [])[0]["name"] == "connect"


def test_config_image_puts_the_ca_where_openssl_looks_it_up(ca_pem):
    """apk has no CA option and an Alpine base has no update-ca-certificates, so `apk add` over https only trusts
    Gitea when the CA sits in OpenSSL's default directory under its subject hash."""
    import subprocess
    from outpost import pki
    h = pki.subject_hash(ca_pem)
    assert h == subprocess.run(["openssl", "x509", "-noout", "-subject_hash"], input=ca_pem.encode(), capture_output=True).stdout.decode().strip()
    image = clients.config_image_files(G, ca_pem, None, [], ca_hash=h)
    assert image[f"/etc/ssl/certs/{h}.0"][0] == image["/etc/outpost/ca.crt"][0] == ca_pem.encode()
    assert not any(p.startswith("/etc/ssl/") for p in clients.config_image_files(G, ca_pem, None, []))
