import json

from outpost import client_config, egress, versions
from outpost.endpoints import GiteaEndpoint
from outpost.gitea import AlpineKey
from outpost.infra import compose, k3s, node, swarm, verify
from outpost.infra.context import NodeContext
from tests.conftest import FakeHost

G = GiteaEndpoint(name="gitea.internal", ip="10.0.0.5", org="pkgs")
CTX = NodeContext(
    gitea=G,
    ca_pem="-----BEGIN CERTIFICATE-----\nca\n",
    arch="amd64",
    distros=["bookworm"],
    allow=["203.0.113.9"],
    infra_name="dev",
)


def test_client_files_point_everything_at_gitea():
    files = client_config.client_files(G, "CA", "KEY", ["bookworm", "noble"])
    assert (
        files["/etc/pip.conf"]
        == "[global]\nindex-url = https://gitea.internal/api/packages/pkgs/pypi/simple\ncert = /etc/outpost/ca.crt\n"
    )
    assert (
        files["/etc/npmrc"]
        == files["/usr/local/etc/npmrc"]
        == "registry=https://gitea.internal/api/packages/pkgs/npm/\ncafile=/etc/outpost/ca.crt\n"
    )
    assert files["/etc/apt/sources.list.d/outpost.list"].splitlines() == [
        "deb [signed-by=/etc/outpost/gitea-apt.asc] https://gitea.internal/api/packages/pkgs/debian bookworm main",
        "deb [signed-by=/etc/outpost/gitea-apt.asc] https://gitea.internal/api/packages/pkgs/debian noble main",
    ]
    assert files["/etc/outpost/gitea-apt.asc"] == "KEY"
    image = client_config.config_image_files(G, "CA", "KEY", ["bookworm"])
    assert image["/etc/apt/sources.list.d/debian.sources"][0].startswith(b"# Disabled by Outpost")
    host = client_config.node_files(G, "CA", "10.0.0.11")
    daemon = json.loads(host["/etc/docker/daemon.json"])
    assert (
        daemon == {"dns": ["10.0.0.11"]} and "registry-mirrors" not in daemon
    )  # images are named by their Gitea copy, nothing is rewritten
    assert host["/etc/docker/certs.d/gitea.internal/ca.crt"] == "CA"
    registries = k3s.registries_yaml(CTX)
    assert (
        "docker.io" not in registries
        and "ca_file: /etc/outpost/ca.crt" in registries
        and 'endpoint:\n      - "https://gitea.internal"' in registries
    )
    for module in (compose, swarm):
        checks = dict(module.verifications(CTX))
        assert not any("mirror" in name or "mirror" in cmd for name, cmd in checks.items())
        assert "test -s /etc/docker/certs.d/gitea.internal/ca.crt" in checks["docker trusts and reaches gitea registry"]
        assert "https://gitea.internal/v2/" in checks["docker trusts and reaches gitea registry"]
    assert "bind 10.0.0.11" in host["/etc/outpost/Corefile"] and "hosts /etc/hosts" in host["/etc/outpost/Corefile"]
    assert host["/etc/outpost/resolv.conf"] == "nameserver 10.0.0.11\n"
    assert G.config_image == "gitea.internal/pkgs/config:latest"
    assert G.generic("docker", "1", "d.tgz") == "https://gitea.internal/api/packages/pkgs/generic/docker/1/d.tgz"


APK_KEY = AlpineKey(name="pkgs@6f2a9c1e.rsa.pub", content="RSA KEY")


def test_client_files_for_apk_and_rpm_point_at_gitea_only():
    files = client_config.client_files(
        G,
        "CA",
        None,
        [],
        apk_key=APK_KEY,
        apk_branches=["v3.20", "v3.19"],
        rpm_key="RPM KEY",
        rpm_distros=["rocky9", "fedora40"],
    )
    assert (
        files["/etc/apk/repositories"]
        == "https://gitea.internal/api/packages/pkgs/alpine/v3.20/main\nhttps://gitea.internal/api/packages/pkgs/alpine/v3.19/main\n"
    )
    assert (
        files["/etc/apk/keys/pkgs@6f2a9c1e.rsa.pub"] == "RSA KEY"
    )  # the name Gitea signs with, or apk ignores the index
    repo = files["/etc/yum.repos.d/outpost.repo"]
    assert repo.startswith("[outpost-rocky9]\n") and "[outpost-fedora40]\n" in repo
    assert (
        "baseurl=https://gitea.internal/api/packages/pkgs/rpm/rocky9\n" in repo
        and "baseurl=https://gitea.internal/api/packages/pkgs/rpm/fedora40\n" in repo
    )
    assert (
        repo.count("gpgkey=file:///etc/outpost/gitea-rpm.asc\n") == 2
        and repo.count("sslcacert=/etc/outpost/ca.crt\n") == 2
        and "gpgcheck=1" in repo
    )
    assert files["/etc/outpost/gitea-rpm.asc"] == "RPM KEY"
    assert "/etc/apt/sources.list.d/outpost.list" not in files and "/etc/outpost/gitea-apt.asc" not in files
    # without branches or distros nothing of theirs is written, in particular no key under /etc/apk/keys
    none = client_config.client_files(G, "CA", "KEY", ["bookworm"], apk_key=APK_KEY, rpm_key="RPM KEY")
    assert not any(p.startswith(("/etc/apk/", "/etc/yum.repos.d/")) or p == client_config.PATH_RPM_KEY for p in none)
    assert (
        G.apk_repo == "https://gitea.internal/api/packages/pkgs/alpine"
        and G.rpm_repo == "https://gitea.internal/api/packages/pkgs/rpm"
    )


def test_config_image_blanks_stock_os_sources_and_stays_text_only():
    image = client_config.config_image_files(
        G, "CA", "KEY", ["bookworm"], apk_key=APK_KEY, apk_branches=["v3.20"], rpm_key="RPM KEY", rpm_distros=["rocky9"]
    )
    assert (
        image["/etc/apk/repositories"][0] == b"https://gitea.internal/api/packages/pkgs/alpine/v3.20/main\n"
    )  # Gitea lines, not blanked
    assert image["/etc/apk/keys/pkgs@6f2a9c1e.rsa.pub"][0] == b"RSA KEY"
    for stock in (
        "rocky.repo",
        "rocky-extras.repo",
        "almalinux-baseos.repo",
        "almalinux-appstream.repo",
        "fedora.repo",
        "fedora-updates.repo",
        "centos.repo",
    ):
        assert image[f"/etc/yum.repos.d/{stock}"][0].startswith(b"# Disabled by Outpost"), stock
    assert image["/etc/yum.repos.d/outpost.repo"][0].startswith(b"[outpost-rocky9]")
    assert image["/etc/apt/sources.list.d/debian.sources"][0].startswith(b"# Disabled by Outpost")
    assert all(
        mode == 0o644 and b"\x00" not in content for content, mode in image.values()
    )  # text files only, no binaries
    # with no Alpine branches the stock /etc/apk/repositories is blanked and no key dir entry is made
    bare = client_config.config_image_files(G, "CA", None, [])
    assert bare["/etc/apk/repositories"][0].startswith(b"# Disabled by Outpost") and not any(
        p.startswith("/etc/apk/keys/") for p in bare
    )
    assert (
        bare["/etc/yum.repos.d/rocky.repo"][0].startswith(b"# Disabled by Outpost")
        and "/etc/yum.repos.d/outpost.repo" not in bare
    )
    assert (
        "/etc/apk/repositories" not in client_config.disabled_os_sources(["v3.20"])
        and "/etc/apk/repositories" in client_config.disabled_os_sources()
    )


def test_prepare_node_on_alpine_configures_apk_and_parks_the_stock_repositories():
    ctx = NodeContext(
        gitea=G, ca_pem="CA", arch="amd64", distros=["bookworm"], apk_branches=["v3.19"], infra_name="dev"
    )
    host = FakeHost(
        "alp-1",
        facts={
            "id": "alpine",
            "id_like": "",
            "version_id": "3.20.3",
            "codename": "",
            "arch": "x86_64",
            "ip": "10.0.0.41",
        },
        responses=[(r"api/v1/version", "ok")],
    )
    node.prepare_node(host, ctx, log=lambda *_: None)
    joined = "\n".join(host.scripts)
    step = next(s for s in host.scripts if "/etc/apk/keys" in s)
    assert "find /etc/apk/keys -name 'pkgs@*.rsa.pub' -delete" in step
    assert (
        "cd /etc/apk/keys && curl --cacert /etc/outpost/ca.crt -fsSL -J -O https://gitea.internal/api/packages/pkgs/alpine/key"
        in step
    )  # -J keeps Gitea's file name
    assert (
        "[ ! -f key ] || mv key pkgs.rsa.pub" in step
    )  # the documented fallback when Gitea sends no Content-Disposition
    assert "mv /etc/apk/repositories /etc/apk/repositories.disabled" in step and "> /etc/apk/repositories" in step
    assert (
        "alpine/v3.19/main/x86_64/APKINDEX.tar.gz" in step and "alpine/v3.20/main/x86_64/APKINDEX.tar.gz" in step
    )  # uploaded branch + the host's own
    assert "echo https://gitea.internal/api/packages/pkgs/alpine/v3.20/main" in step
    assert "sources.disabled" not in joined and "yum.repos.d" not in joined and "dists/bookworm/Release" not in joined
    assert (
        "update-ca-certificates" in joined
    )  # Alpine's ca-certificates package reads /usr/local/share/ca-certificates too
    assert (
        host.files["/etc/pip.conf"].startswith("[global]") and "/etc/apk/repositories" not in host.files
    )  # written by the step, not as a file


def test_prepare_node_on_rocky_configures_dnf_and_parks_the_stock_repo_files():
    ctx = NodeContext(
        gitea=G, ca_pem="CA", arch="amd64", distros=["bookworm"], rpm_distros=["fedora40"], infra_name="dev"
    )
    host = FakeHost(
        "rocky-1",
        facts={
            "id": "rocky",
            "id_like": "rhel centos fedora",
            "version_id": "9.4",
            "codename": "",
            "arch": "x86_64",
            "ip": "10.0.0.51",
        },
        responses=[(r"api/v1/version", "ok")],
    )
    node.prepare_node(host, ctx, log=lambda *_: None)
    joined = "\n".join(host.scripts)
    step = next(s for s in host.scripts if "/etc/yum.repos.d" in s)
    assert (
        "curl --cacert /etc/outpost/ca.crt -fsSL https://gitea.internal/api/packages/pkgs/rpm/repository.key -o /etc/outpost/gitea-rpm.asc"
        in step
    )
    assert (
        "mkdir -p /etc/yum.repos.d.disabled" in step
        and '!= outpost.repo ] && mv "$f" /etc/yum.repos.d.disabled/' in step
    )
    assert (
        "rpm/fedora40/repodata/repomd.xml" in step and "rpm/rocky9/repodata/repomd.xml" in step
    )  # uploaded distro + the host's own
    assert (
        "baseurl=https://gitea.internal/api/packages/pkgs/rpm/rocky9" in step
        and "> /etc/yum.repos.d/outpost.repo" in step
    )
    assert "sources.disabled" not in joined and "/etc/apk/" not in joined
    assert "update-ca-trust" in joined


def test_verify_checks_pick_the_command_by_family_on_the_node():
    checks = dict(verify.node_verifications(CTX))
    assert "apt sources are gitea only" not in checks and "apt update works offline" not in checks
    sources, index = checks["package sources are gitea only"], checks["package index updates offline"]
    for cmd in (sources, index):
        assert (
            cmd.startswith('. /etc/os-release 2>/dev/null; case " ${ID:-} ${ID_LIKE:-} " in *alpine*)')
            and "*rhel*|*fedora*|*centos*" in cmd
            and cmd.endswith("esac")
        )
    assert "grep -v '^#' /etc/apk/repositories" in sources and "grep -v https://gitea.internal" in sources
    assert "grep -lsE '^enabled *= *1' /etc/yum.repos.d/*.repo | grep -v outpost.repo" in sources
    assert "grep -rhs '^deb ' /etc/apt/sources.list" in sources
    assert "apk update" in index and "dnf makecache" in index and "apt-get update -qq" in index
    script = verify.verification_script(verify.node_verifications(CTX))
    assert "CHECK|package sources are gitea only|" in script and "CHECK|package index updates offline|" in script


def test_egress_script():
    script = egress.seal_script("10.0.0.5", ["203.0.113.9", "10.0.0.0/8"])
    assert 'GITEA_IP="10.0.0.5"' in script
    assert 'ALLOW="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 203.0.113.9"' in script
    assert "DOCKER-USER" in script and "hook $ipt filter FORWARD" in script and "hook $ipt security FORWARD" in script
    assert "REJECT" in script and "-j DROP" in script and "--dport 53" in script
    host = FakeHost()
    egress.install_seal(host, "10.0.0.5", [])
    assert (
        host.files[egress.SCRIPT_PATH].startswith("#!/bin/bash")
        and "Before=docker.service" in host.files[egress.UNIT_PATH]
    )
    assert host.ran("systemctl enable outpost-egress")
    egress.seal_host(host, "test")
    assert host.scripts[-1].endswith("outpost-egress seal test")
    assert egress.seal_status(FakeHost(responses=[(r"status", "sealed")])) == "sealed"


def test_compose_setup_configures_node_then_installs_docker_from_gitea():
    host = FakeHost("dev-1", responses=[(r"api/v1/version", '{"version":"28.2.0"}')])
    result = compose.setup(CTX, [host])
    assert result["nodes"]["dev-1"]["ip"] == "10.0.0.11"
    assert host.files["/etc/pip.conf"].startswith("[global]")
    assert host.files["/etc/docker/certs.d/gitea.internal/ca.crt"] == CTX.ca_pem
    assert host.files["/etc/systemd/system/docker.service"].count("ExecStart=/usr/local/bin/dockerd") == 1
    joined = "\n".join(host.scripts)
    assert "10.0.0.5 gitea.internal" in joined and "sed -i" not in joined  # /etc/hosts, rewritten in place
    assert (
        "Before=outpost-dns.service docker.service" in host.files["/etc/systemd/system/outpost-hosts.service"]
    )  # and restored at boot
    assert "echo '10.0.0.5 gitea.internal' >> /etc/hosts" in host.files["/usr/local/sbin/outpost-hosts"] and host.ran(
        "systemctl enable outpost-hosts"
    )
    assert "update-ca-certificates" in joined  # system trust
    assert 'mv "$f" /etc/apt/sources.disabled/' in joined  # stock apt sources parked
    assert "dists/bookworm/Release" in joined and "dists/noble/Release" in joined  # host codename added
    probe = next(line for line in joined.splitlines() if "dists/noble/Release" in line)
    assert (
        "curl " in probe and " -fsI " not in probe and "-o /dev/null" in probe
    )  # GET: Gitea's Debian dists/ answers HEAD with 405
    assert f"generic/docker/{versions.DOCKER}/docker-{versions.DOCKER}.tgz" in joined
    assert "--cacert /etc/outpost/ca.crt" in joined
    seal_index = next(i for i, s in enumerate(host.scripts) if "outpost-egress seal" in s)
    dns_index = next(i for i, s in enumerate(host.scripts) if "coredns.tgz" in s)
    docker_index = next(i for i, s in enumerate(host.scripts) if "docker.tgz" in s)
    assert seal_index < dns_index < docker_index, (
        "DNS and Docker are installed after the seal, proving they come from Gitea"
    )
    assert "coredns" in host.files["/etc/systemd/system/outpost-dns.service"]


def test_compose_setup_skips_installed_docker():
    host = FakeHost(
        responses=[(r"api/v1/version", "ok"), (r"docker --version", f"Docker version {versions.DOCKER}, build x")]
    )
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
    manager = FakeHost(
        "p1",
        facts={"ip": "10.0.0.21", "codename": "noble"},
        responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "inactive"), (r"join-token", "SWMTKN-1-abc")],
    )
    worker = FakeHost(
        "p2",
        facts={"ip": "10.0.0.22", "codename": "noble"},
        responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "inactive")],
    )
    result = swarm.setup(CTX, [manager, worker])
    assert result["manager"] == "p1"
    assert manager.ran("docker swarm init --advertise-addr 10.0.0.21")
    assert worker.ran("docker swarm join --token SWMTKN-1-abc 10.0.0.21:2377")
    again = FakeHost(
        "p1",
        facts={"ip": "10.0.0.21"},
        responses=[(r"api/v1/version", "ok"), (r"LocalNodeState", "active"), (r"join-token", "T")],
    )
    swarm.setup(CTX, [again])
    assert not again.ran("swarm init")


def test_k3s_server_and_agent():
    server = FakeHost(
        "k1",
        facts={"ip": "10.0.0.31", "codename": "noble"},
        responses=[(r"api/v1/version", "ok"), (r"node-token", "K10abc::server:xyz")],
    )
    agent = FakeHost("k2", facts={"ip": "10.0.0.32", "codename": "noble"}, responses=[(r"api/v1/version", "ok")])
    result = k3s.setup(CTX, [server, agent])
    assert result["server"] == "k1"
    assert (
        "docker.io" not in server.files["/etc/rancher/k3s/registries.yaml"]
        and "ca_file: /etc/outpost/ca.crt" in server.files["/etc/rancher/k3s/registries.yaml"]
    )
    assert server.ran(
        "INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_EXEC='server --node-ip 10.0.0.31 --resolv-conf /etc/outpost/resolv.conf'"
    )
    custom = NodeContext(gitea=G, ca_pem="CA", arch="amd64", args="--disable traefik")
    s2 = FakeHost("k9", facts={"ip": "10.0.0.39"}, responses=[(r"api/v1/version", "ok"), (r"node-token", "T")])
    k3s.setup(custom, [s2])
    assert s2.ran("--resolv-conf /etc/outpost/resolv.conf --disable traefik'")
    assert server.ran("k3s-airgap-images-amd64.tar.gz")
    assert agent.ran("K3S_URL=https://10.0.0.31:6443 K3S_TOKEN=K10abc::server:xyz")
    assert agent.ran("INSTALL_K3S_EXEC='agent --resolv-conf /etc/outpost/resolv.conf'")


def test_verify_script_and_parse():
    checks = verify.node_verifications(CTX) + compose.verifications(CTX)
    script = verify.verification_script(checks)
    assert script.count("CHECK|") == 2 * len(checks)
    output = "CHECK|gitea in /etc/hosts|OK|\nCHECK|public internet rejected|FAIL|curl: (7) Failed\nnoise\n"
    assert verify.parse_verifications(output) == [
        {"name": "gitea in /etc/hosts", "ok": True, "detail": ""},
        {"name": "public internet rejected", "ok": False, "detail": "curl: (7) Failed"},
    ]
    host = FakeHost(responses=[(r"CHECK", "CHECK|a|OK|x\n")])
    assert verify.run_verifications(host, [("a", "true")]) == [{"name": "a", "ok": True, "detail": "x"}]
    assert verify.run_verifications(FakeHost(), [("a", "true")])[0]["name"] == "connect"


def test_config_image_puts_the_ca_where_openssl_looks_it_up(ca_pem):
    """apk has no CA option and an Alpine base has no update-ca-certificates, so `apk add` over https only trusts
    Gitea when the CA sits in OpenSSL's default directory under its subject hash."""
    import subprocess

    from outpost import pki

    h = pki.subject_hash(ca_pem)
    assert (
        h
        == subprocess.run(["openssl", "x509", "-noout", "-subject_hash"], input=ca_pem.encode(), capture_output=True)
        .stdout.decode()
        .strip()
    )
    image = client_config.config_image_files(G, ca_pem, None, [], ca_hash=h)
    assert image[f"/etc/ssl/certs/{h}.0"][0] == image["/etc/outpost/ca.crt"][0] == ca_pem.encode()
    assert not any(p.startswith("/etc/ssl/") for p in client_config.config_image_files(G, ca_pem, None, []))


def run_sources_check(tmp_path, family: str, files: dict[str, str]):
    """Run one family's sources check against a fake root: every /etc/ path is moved under tmp_path."""
    import subprocess

    for path, content in files.items():
        target = tmp_path / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for d in ("etc/apt/sources.list.d", "etc/yum.repos.d", "etc/apk"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    cmd = verify.gitea_only_sources_by_family(G)[family].replace("/etc/", f"{tmp_path}/etc/")
    out = subprocess.run(["bash", "-c", f"( {cmd} ) 2>&1"], capture_output=True, text=True)
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    return out.returncode, last.replace(str(tmp_path), "")


def test_sources_check_fails_without_a_gitea_source(tmp_path):
    """From 0.1.0 outpost.list was empty on every apt node (the setup probe used HEAD, which Gitea's
    Debian registry refuses) and the check, which only looked for foreign sources, passed."""
    apt = "/etc/apt/sources.list.d/outpost.list"
    rc, detail = run_sources_check(tmp_path / "apt-empty", "apt", {apt: ""})
    assert rc != 0 and detail.startswith(
        "no apt sources point at gitea: /etc/apt/sources.list.d/outpost.list has no deb line"
    )
    rc, detail = run_sources_check(tmp_path / "apt-ok", "apt", {apt: client_config.apt_source_line(G, "noble") + "\n"})
    assert rc == 0 and detail == "apt sources from gitea: 1"
    rc, detail = run_sources_check(
        tmp_path / "apt-foreign",
        "apt",
        {
            apt: client_config.apt_source_line(G, "noble") + "\n",
            "/etc/apt/sources.list": "deb http://archive.ubuntu.com/ubuntu noble main\n",
        },
    )
    assert rc != 0 and "archive.ubuntu.com" in detail  # a foreign line fails it even with a Gitea one next to it

    rc, detail = run_sources_check(tmp_path / "apk-empty", "apk", {client_config.PATH_APK_REPOS: "# nothing\n"})
    assert rc != 0 and detail.startswith("no apk repositories point at gitea: /etc/apk/repositories is empty")
    rc, detail = run_sources_check(
        tmp_path / "apk-ok", "apk", {client_config.PATH_APK_REPOS: client_config.apk_repository_line(G, "v3.20") + "\n"}
    )
    assert rc == 0 and detail == "apk repositories from gitea: 1"

    rc, detail = run_sources_check(tmp_path / "rpm-empty", "rpm", {client_config.PATH_RPM_REPO: "\n"})
    assert rc != 0 and detail.startswith(
        "no rpm repositories point at gitea: /etc/yum.repos.d/outpost.repo has no section"
    )
    rc, detail = run_sources_check(tmp_path / "rpm-missing", "rpm", {})
    assert rc != 0 and detail.startswith("no rpm repositories point at gitea")
    two = client_config.rpm_repo_section(G, "rocky9") + "\n" + client_config.rpm_repo_section(G, "fedora40")
    rc, detail = run_sources_check(tmp_path / "rpm-ok", "rpm", {client_config.PATH_RPM_REPO: two})
    assert rc == 0 and detail == "rpm repositories from gitea: 2"


def test_index_update_check_takes_the_tools_exit_status(tmp_path):
    """`apt-get update | tail -3` was the check before: tail's status, so a failed update passed."""
    import subprocess

    (tmp_path / "apt-get").write_text("#!/bin/sh\necho 'E: Failed to fetch https://gitea.internal/...'\nexit 100\n")
    (tmp_path / "apt-get").chmod(0o755)
    cmd = dict(verify.node_verifications(CTX))["package index updates offline"]
    apt = cmd.rsplit(" *) ", 1)[1].rsplit(" ;; esac", 1)[0]  # the apt branch, the case's last
    out = subprocess.run(
        ["bash", "-c", f"( {apt} )"], capture_output=True, text=True, env={"PATH": f"{tmp_path}:/usr/bin:/bin"}
    )
    assert out.returncode != 0 and "E: Failed to fetch" in out.stdout


# -- bugs: what runs under Host.run's `set -euo pipefail` ------------------------------------

OS_RELEASES = {  # trimmed from the real files: Debian and Alpine have no ID_LIKE
    "debian": 'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n',
    "alpine": "ID=alpine\nVERSION_ID=3.20.3\n",
    "rocky": 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n',
}


def run_like_host_run(script: str, os_release, path: str = "/usr/bin:/bin"):
    """Run a script the way Host.run does (`set -euo pipefail` on top), reading os_release instead of
    /etc/os-release."""
    import subprocess

    body = script.replace("/etc/os-release", str(os_release))
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + body], capture_output=True, text=True, env={"PATH": path}
    )


def test_by_family_works_without_id_like_under_set_u(tmp_path):
    """`case " $ID $ID_LIKE "` stopped with "ID_LIKE: unbound variable" on Debian and Alpine, so their
    "package sources are gitea only" and "package index updates offline" verifications always failed."""
    snippet = verify.by_family(apt="echo apt", apk="echo apk", rpm="echo rpm")
    for distro, family in (("debian", "apt"), ("alpine", "apk"), ("rocky", "rpm")):
        os_release = tmp_path / f"{distro}-os-release"
        os_release.write_text(OS_RELEASES[distro])
        out = run_like_host_run(snippet, os_release)
        assert (out.returncode, out.stdout.strip()) == (0, family), out.stderr


def test_upgrade_script_works_without_id_like_under_set_u(tmp_path):
    """The same bug stopped `outpost infra upgrade` on Debian and Alpine before it ran anything."""
    from outpost.infra import UPGRADE_SCRIPT

    tools = tmp_path / "bin"
    tools.mkdir()
    for tool, says in (("apk", "apk $1"), ("apt-get", "apt-get $1; echo '0 upgraded, 0 newly installed'")):
        (tools / tool).write_text(f"#!/bin/sh\necho {says}\n")
        (tools / tool).chmod(0o755)
    for distro, ran in (("debian", "apt-get update"), ("alpine", "apk upgrade")):
        os_release = tmp_path / f"{distro}-os-release"
        os_release.write_text(OS_RELEASES[distro])
        out = run_like_host_run(UPGRADE_SCRIPT, os_release, path=f"{tools}:/usr/bin:/bin")
        assert out.returncode == 0 and ran in out.stdout, out.stderr


def test_facts_script_works_without_pretty_name_under_set_u(tmp_path):
    """PRETTY_NAME is optional in os-release; `echo "pretty=$PRETTY_NAME"` stopped the facts script
    under `set -u`, so `outpost hosts` saw such a server as unreachable."""
    from outpost.hosts import FACTS_SCRIPT

    os_release = tmp_path / "os-release"
    os_release.write_text(OS_RELEASES["debian"])
    out = run_like_host_run(FACTS_SCRIPT, os_release)
    assert out.returncode == 0, out.stderr
    assert "id=debian" in out.stdout.splitlines() and "pretty=" in out.stdout.splitlines()
    out = run_like_host_run(FACTS_SCRIPT, tmp_path / "missing")  # no os-release at all
    assert out.returncode == 0 and "id=" in out.stdout.splitlines(), out.stderr


def test_a_version_verification_survives_a_tool_that_writes_after_the_match(tmp_path):
    """Flaky: `coredns -version | grep -q 1.15.0` failed now and then after a reboot. grep -q exits at
    the first match, coredns then writes its second line into a closed pipe and dies of SIGPIPE, and
    `set -o pipefail` makes that the verification's result."""
    writes_twice = "{ echo CoreDNS-1.15.0; sleep 0.3; echo linux/amd64; }"
    assert run_like_host_run(f"{writes_twice} | grep -q 1.15.0", tmp_path / "os-release").returncode != 0
    script = verify.verification_script([("coredns", verify.output_contains(writes_twice, "1.15.0"))])
    out = run_like_host_run(script, tmp_path / "os-release")
    assert verify.parse_verifications(out.stdout) == [{"name": "coredns", "ok": True, "detail": ""}], out.stderr
    script = verify.verification_script([("coredns", verify.output_contains(writes_twice, "1.16.0"))])
    assert verify.parse_verifications(run_like_host_run(script, tmp_path / "x").stdout)[0]["ok"] is False


def test_no_verification_pipes_into_grep_q():
    """Every verification that looks for a version or a source reads the whole output (output_contains)."""
    from outpost.config import ProxyConfig
    from outpost.infra import runner
    from outpost.infra.proxy import host_verifications

    every = (
        verify.node_verifications(CTX)
        + compose.verifications(CTX)
        + swarm.verifications(CTX)
        + k3s.verifications(CTX)
        + runner.verifications()
        + host_verifications(CTX, ProxyConfig(host="gitea-1", allow=["api.twilio.com"]))
    )
    piped = [name for name, command in every if "version | grep -q" in command or "policy tinyproxy | grep" in command]
    assert piped == [] and any("coredns" in name for name, _ in every)


def test_k3s_args_reach_the_installer_exactly(tmp_path):
    """INSTALL_K3S_EXEC was quoted with Python's repr(): an `args:` with a ' got double quotes, in which
    the shell expands $ and backticks."""
    import subprocess

    args = "--node-label note='a b' --kubelet-arg=x=$(touch " + str(tmp_path / "ran") + ")"
    host = FakeHost("k1", facts={"ip": "10.0.0.11", "arch": "x86_64"}, responses=[(r"api/v1/version", "ok")])
    k3s.setup(NodeContext(gitea=G, ca_pem="CA", arch="amd64", args=args), [host], log=lambda *_: None)
    install = next(s for s in host.scripts if "sh /tmp/k3s-install.sh" in s)
    line = next(line for line in install.splitlines() if "INSTALL_K3S_EXEC=" in line)
    print_exec = line.replace("sh /tmp/k3s-install.sh", "sh -c 'printf %s \"$INSTALL_K3S_EXEC\"'")
    out = subprocess.run(["bash", "-c", print_exec], capture_output=True, text=True)
    assert out.stdout == f"server --node-ip 10.0.0.11 --resolv-conf /etc/outpost/resolv.conf {args}"
    assert not (tmp_path / "ran").exists()  # nothing in args ran on the node


def test_gitea_and_the_layer_below_do_not_load_infra():
    """egress, hostsfile and client_config sit below gitea, so gitea setup and upload no longer reach
    into infra/ (layer 3); and infra/verify no longer imports the proxy package inside a function."""
    import subprocess
    import sys

    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, outpost.gitea; print(sorted(m for m in sys.modules if m.startswith('outpost.infra')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert loaded == "[]"
    import importlib
    import inspect

    for name in ("outpost.infra.verify", "outpost.infra.proxy.verify", "outpost.infra.proxy.setup"):
        source = inspect.getsource(importlib.import_module(name))
        assert "    from outpost" not in source and "    import outpost" not in source, name


def test_a_reason_with_quotes_reaches_the_egress_log_as_typed(tmp_path):
    """The dashboard asks "Why open the internet?" and passes the answer as --reason. It went into the
    command unquoted: "can't build" broke it, and $(...) ran on the server as root."""
    import subprocess

    host = FakeHost(responses=[(r"outpost-egress", "open\n")])
    reason = "can't build $(touch " + str(tmp_path / "ran") + ")"
    egress.open_host(host, reason)
    log = tmp_path / "egress.log"
    stub = tmp_path / "outpost-egress"  # logs its arguments the way the real script does: log "open ${*:2}"
    stub.write_text(f'#!/bin/bash\necho "$1 ${{*:2}}" > {log}\n')
    stub.chmod(0o755)
    subprocess.run(["bash", "-c", host.scripts[-1].replace(egress.SCRIPT_PATH, str(stub))], check=True)
    assert log.read_text() == f"open {reason}\n" and not (tmp_path / "ran").exists()
