import json

from outpost import versions
from outpost.infra import clients, compose, egress, k3s, swarm, verify
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
