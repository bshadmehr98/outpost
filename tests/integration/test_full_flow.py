"""The whole flow against the lab: throwaway Ubuntu containers running systemd and sshd.

    OUTPOST_INTEGRATION=1 pytest tests/integration/test_full_flow.py -v -s

`outpost lab up` starts them; then Gitea is installed on one, the box is uploaded, a Compose node,
a two-node Swarm and a k3s server are set up, sealed and verified, and the smoke checks build and
run containers on them with the internet blocked.
Set OUTPOST_BOX=/path/to/box to start from a box of an earlier `outpost fetch`; fetch then only adds what is missing.
The rig lives in /tmp/outpost-rig (or OUTPOST_RIG_DIR); with OUTPOST_KEEP_RIG=1 the lab stays up
and you can poke at it with `ssh -F /tmp/outpost-rig/lab/ssh_config lab-node-1`.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from outpost import lab
from outpost.cli import main

pytestmark = [pytest.mark.skipif(os.environ.get("OUTPOST_INTEGRATION") != "1", reason="set OUTPOST_INTEGRATION=1"), pytest.mark.integration]

GITEA_IP = lab.SERVERS["lab-gitea"][0]
GITEA_NAME = "gitea.lab"   # the name `lab up` writes into servers.yml


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kw)


def ssh(alias: str, command: str, check=True):
    return sh("ssh", "-F", str(RIG / "lab" / "ssh_config"), alias, command, check=check)


RIG = Path(os.environ.get("OUTPOST_RIG_DIR", "/tmp/outpost-rig"))


@pytest.fixture(scope="module")
def rig():
    """A platform folder whose servers are the lab containers, exactly what `outpost lab up` gives a user."""
    shutil.rmtree(RIG, ignore_errors=True)
    assert main(["init", str(RIG)]) == 0
    assert main(["--dir", str(RIG), "lab", "up"]) == 0
    # the needs are what `outpost init` ships: needs/demo.yml, covering the checks and the demo
    if os.environ.get("OUTPOST_BOX"):
        shutil.copytree(os.environ["OUTPOST_BOX"], RIG / "box")
    yield RIG
    if os.environ.get("OUTPOST_KEEP_RIG") != "1":
        assert main(["--dir", str(RIG), "lab", "down"]) == 0


def test_1_fetch(rig):
    """With OUTPOST_BOX this only tops up what the copied box lacks (fetch skips what is there)."""
    assert main(["--dir", str(rig), "fetch"]) == 0
    manifest = json.loads((rig / "box" / "manifest.json").read_text())
    paths = {item["path"] for item in manifest["items"]}
    assert any(p.startswith("files/gitea-runner/") for p in paths) and any(p.startswith("pypi/gunicorn-") for p in paths)


PREVIOUS_GITEA = os.environ.get("OUTPOST_PREVIOUS_GITEA", "28.1.0")


def test_2a_install_the_previous_gitea(rig, monkeypatch):
    """Install the release before the pinned one first, so test_2 is a real upgrade with data in it."""
    from outpost import versions
    from outpost.http import Http
    binary = rig / "box" / "files" / "gitea" / PREVIOUS_GITEA / "gitea"
    if not binary.exists():
        try:
            Http().download(f"https://dl.gitea.com/gitea/{PREVIOUS_GITEA}/gitea-{PREVIOUS_GITEA}-linux-amd64", binary, quiet=True)
        except Exception as e:  # no such release, or no internet: the upgrade part is skipped, the rest still runs
            pytest.skip(f"could not download Gitea {PREVIOUS_GITEA}: {e}")
    monkeypatch.setattr(versions, "GITEA", PREVIOUS_GITEA)
    assert main(["--dir", str(rig), "gitea", "setup"]) == 0
    assert PREVIOUS_GITEA in ssh("lab-gitea", "/usr/local/bin/gitea --version").stdout
    token = (rig / "secrets" / "gitea-token").read_text().strip()
    # something to survive the upgrade: a repository
    code = sh("curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "-u", f"outpost:{token}", "-H", "Content-Type: application/json",
              "-d", '{"name":"survives","auto_init":true}', f"https://{GITEA_IP}/api/v1/orgs/pkgs/repos").stdout
    assert code == "201", code


def test_2_gitea_setup(rig):
    """Installs the pinned Gitea; when test_2a ran, this is an upgrade: backup first, then the new binary, data intact."""
    from outpost import versions
    before = ssh("lab-gitea", "/usr/local/bin/gitea --version", check=False).stdout
    assert main(["--dir", str(rig), "gitea", "setup"]) == 0
    state = json.loads((rig / "state" / "gitea.json").read_text())
    assert state["ip"] == GITEA_IP and (rig / "secrets" / "gitea-token").exists()
    assert "sealed" in ssh("lab-gitea", "/usr/local/sbin/outpost-egress status").stdout
    assert versions.GITEA in ssh("lab-gitea", "/usr/local/bin/gitea --version").stdout
    if PREVIOUS_GITEA in before:
        backups = list((rig / "backups").glob("gitea-*.tar.gz"))
        assert backups, "an upgrade must leave a backup behind"
        token = (rig / "secrets" / "gitea-token").read_text().strip()
        assert sh("curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "-u", f"outpost:{token}", f"https://{GITEA_IP}/api/v1/repos/pkgs/survives").stdout == "200"
    # a second run changes nothing and still succeeds
    assert main(["--dir", str(rig), "gitea", "setup"]) == 0
    # the token has narrow scopes: it cannot list users (admin) ...
    token = (rig / "secrets" / "gitea-token").read_text().strip()
    assert sh("curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "-u", f"outpost:{token}", f"https://{GITEA_IP}/api/v1/admin/users").stdout == "403"
    # ... and `outpost hosts` knows the machine
    assert main(["--dir", str(rig), "hosts"]) == 0
    assert json.loads((rig / "state" / "hosts" / "lab-gitea.json").read_text())["installed"]["gitea"] == versions.GITEA


def test_2b_backup_and_restore(rig):
    assert main(["--dir", str(rig), "gitea", "backup"]) == 0
    archive = sorted((rig / "backups").glob("gitea-*.tar.gz"))[-1]
    assert archive.stat().st_size > 1000
    assert main(["--dir", str(rig), "gitea", "restore", str(archive)]) == 0
    token = (rig / "secrets" / "gitea-token").read_text().strip()
    assert sh("curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Authorization: token {token}", f"https://{GITEA_IP}/api/v1/user").stdout == "200"
    ssh("lab-gitea", "rm -rf /var/lib/gitea/data.before-restore-*")


def test_3_upload(rig):
    assert main(["--dir", str(rig), "upload"]) == 0
    assert json.loads((rig / "state" / "upload.json").read_text())["distros"] == ["bookworm"]
    assert main(["--dir", str(rig), "upload"]) == 0  # everything already there


def test_4_infra_setup_and_verify(rig):
    assert main(["--dir", str(rig), "infra", "setup", "dev"]) == 0
    report = json.loads((rig / "state" / "verify-dev.json").read_text())
    assert report["result"] == "ok", json.dumps(report, indent=1)
    names = {r["name"] for r in report["nodes"]["lab-node-1"]}
    assert {"public internet rejected", "docker trusts and reaches gitea registry", "container egress hooked", "package sources are gitea only", "package index updates offline", "node dns for containers running"} <= names
    # nothing rewrites image names: a bare Docker Hub name is rejected, the Gitea name of the same image works
    assert ssh("lab-node-1", "docker pull alpine:3.20", check=False).returncode != 0
    assert ssh("lab-node-1", f"docker pull {GITEA_NAME}/pkgs/library/alpine:3.20", check=False).returncode == 0
    assert main(["--dir", str(rig), "infra", "verify", "dev"]) == 0


def test_5_check(rig):
    assert main(["--dir", str(rig), "check", "--infra", "dev"]) == 0
    results = json.loads((rig / "state" / "check-dev.json").read_text())["results"]
    by_name = {r["name"]: r for r in results}
    assert {"hello-python", "hello-alpine"} <= set(by_name) and all(r["ok"] for r in by_name.values()), json.dumps(results, indent=1)
    assert "Hello from Outpost" in by_name["hello-python"]["steps"][1]["output"]
    assert "curl" in by_name["hello-alpine"]["steps"][1]["output"]   # curl came from Gitea's Alpine registry


def test_6_egress_toggle(rig):
    assert main(["--dir", str(rig), "egress", "dev", "open", "--reason", "test"]) == 0
    assert ssh("lab-node-1", "curl -sSI --max-time 15 -o /dev/null -w '%{http_code}' https://pypi.org/", check=False).stdout.strip() in ("200", "301", "302")
    assert main(["--dir", str(rig), "egress", "dev", "seal"]) == 0
    assert ssh("lab-node-1", "curl -sSI --max-time 5 https://pypi.org/", check=False).returncode != 0
    assert "open test" in ssh("lab-node-1", "cat /var/log/outpost-egress.log").stdout


def test_6b_proxy(rig):
    """The named exceptions: a tinyproxy on lab-gitea lets sealed workloads reach example.org, and only that,
    and only when they ask for it with HTTPS_PROXY. From here on every infrastructure has the proxy settings."""
    servers = rig / "servers.yml"
    servers.write_text(servers.read_text() + "\negress_proxy:\n  allow: [example.org]\n  distro: noble\n")
    assert main(["--dir", str(rig), "fetch", "--only", "apt"]) == 0                  # adds tinyproxy for noble by itself
    assert list((rig / "box" / "apt" / "noble").glob("tinyproxy*.deb"))
    assert main(["--dir", str(rig), "upload"]) == 0
    assert "noble" in json.loads((rig / "state" / "upload.json").read_text())["distros"]
    assert main(["--dir", str(rig), "proxy", "setup"]) == 0
    assert main(["--dir", str(rig), "proxy", "verify"]) == 0
    state = json.loads((rig / "state" / "proxy.json").read_text())
    assert state["url"] == f"http://{GITEA_NAME}:3128" and state["ip"] == GITEA_IP and state["tinyproxy"] and state["verify"]["result"] == "ok"
    # the Gitea host is still sealed for everyone but the proxy's own user
    assert "sealed" in ssh("lab-gitea", "/usr/local/sbin/outpost-egress status").stdout
    assert ssh("lab-gitea", "curl --noproxy '*' -sS --max-time 5 https://example.org/", check=False).returncode != 0
    # the node set up before the proxy existed got its settings
    assert f"HTTPS_PROXY=http://{GITEA_NAME}:3128" in ssh("lab-node-1", "cat /etc/outpost/proxy.env").stdout
    # a container with HTTPS_PROXY reaches example.org through it; without the variables it does not, and
    # through it a name that is not on the list is refused by the proxy (403)
    image = f"{GITEA_NAME}/pkgs/library/python:3.12-slim"
    fetch = "import sys, urllib.request as u; print(u.urlopen('https://' + sys.argv[1] + '/', timeout=20).status)"
    via = f"-e HTTPS_PROXY=http://{GITEA_NAME}:3128 -e https_proxy=http://{GITEA_NAME}:3128"
    unset = "-e HTTPS_PROXY= -e https_proxy= -e HTTP_PROXY= -e http_proxy="   # also overrides the Docker client config
    reached = ssh("lab-node-1", f'docker run --rm {via} {image} python -c "{fetch}" example.org', check=False)
    assert reached.returncode == 0 and "200" in reached.stdout, reached.stderr
    direct = ssh("lab-node-1", f'docker run --rm {unset} {image} python -c "{fetch}" example.org', check=False)
    assert direct.returncode != 0, direct.stdout
    refused = ssh("lab-node-1", f'docker run --rm {via} {image} python -c "{fetch}" example.com', check=False)
    assert refused.returncode != 0 and "403" in refused.stderr, refused.stderr
    # every CONNECT and every refusal is in the proxy's log
    log = ssh("lab-gitea", "cat /var/log/tinyproxy/tinyproxy.log").stdout
    assert "CONNECT example.org:443" in log and 'filtered domain "example.com"' in log
    # and the smoke check that says the same
    assert main(["--dir", str(rig), "check", "hello-proxy", "--infra", "dev"]) == 0
    results = json.loads((rig / "state" / "check-dev.json").read_text())["results"]
    assert [r["name"] for r in results] == ["hello-proxy"] and results[0]["ok"], json.dumps(results, indent=1)

def test_7_swarm(rig):
    """Two nodes: the compose node from before becomes the manager, lab-node-2 joins as a worker."""
    assert main(["--dir", str(rig), "infra", "setup", "prod"]) == 0
    nodes = ssh("lab-node-1", "docker node ls --format '{{.Hostname}} {{.Status}} {{.ManagerStatus}}'").stdout
    assert "lab-node-1 Ready Leader" in nodes and "lab-node-2 Ready" in nodes
    # a service pulls its image from Gitea, by its Gitea name, on whichever node runs it
    ssh("lab-node-1", f"docker service rm hello >/dev/null 2>&1; docker service create -q --name hello --replicas 2 --constraint node.role==worker {GITEA_NAME}/pkgs/library/alpine:3.20 sleep 300")
    for _ in range(60):
        if "2/2" in ssh("lab-node-1", "docker service ls --format '{{.Replicas}}' --filter name=hello").stdout:
            break
        time.sleep(2)
    assert "2/2" in ssh("lab-node-1", "docker service ls --format '{{.Replicas}}' --filter name=hello").stdout
    ssh("lab-node-1", "docker service rm hello")


def test_8_k3s(rig):
    """A k3s server in a privileged container; the hello-k3s check runs a pod from Gitea."""
    assert main(["--dir", str(rig), "infra", "setup", "k8s"]) == 0
    report = json.loads((rig / "state" / "verify-k8s.json").read_text())
    assert report["result"] == "ok", json.dumps(report, indent=1)
    assert main(["--dir", str(rig), "check", "--infra", "k8s"]) == 0


def test_9_demo_through_gitea_actions(rig):
    """Push the demo; the runners on the nodes build it and deploy it to dev, prod and k8s; every
    deployment answers with the pushed commit and reports no internet at build time or now."""
    assert main(["--dir", str(rig), "demo", "push"]) == 0
    state = json.loads((rig / "state" / "demo.json").read_text())
    assert set(state["infras"]) == {"dev", "prod", "k8s"} and (rig / "secrets" / "gitea-package-token").exists()
    token = (rig / "secrets" / "gitea-token").read_text().strip()
    for _ in range(90):
        out = sh("curl", "-sk", "-u", f"outpost:{token}", f"https://{GITEA_IP}/api/v1/repos/pkgs/demo/actions/runs?limit=1", check=False).stdout
        runs = json.loads(out or "{}").get("workflow_runs") or []
        if runs and runs[0]["status"] == "completed":
            break
        time.sleep(10)
    assert runs and runs[0]["conclusion"] == "success", out
    for name, port in (("dev", 8000), ("prod", 8080), ("k8s", 30080)):
        ip = state["infras"][name]["ip"]
        health = json.loads(sh("curl", "-fsS", "--max-time", "15", f"http://{ip}:{port}/healthz").stdout)
        assert health["sha"] == state["commit"] and health["sealed_at_build"] and health["sealed_now"] and health["infra"] == name, health
    assert main(["--dir", str(rig), "demo", "status"]) == 0


def test_10_reboot_keeps_the_seal_and_the_apps(rig):
    """Restarting a lab container is the closest thing to a reboot: systemd starts from scratch, so the
    egress unit, CoreDNS, Docker, k3s and the runner must all come back, sealed, and the demo must serve."""
    state = json.loads((rig / "state" / "demo.json").read_text())
    for alias in ("lab-node-1", "lab-k3s"):
        sh("docker", "restart", alias)
        for _ in range(60):
            if ssh(alias, "systemctl is-active outpost-egress docker k3s 2>/dev/null | grep -c active", check=False).stdout.strip() not in ("", "0"):
                break
            time.sleep(2)
        time.sleep(5)
        assert "sealed" in ssh(alias, "/usr/local/sbin/outpost-egress status").stdout
        assert ssh(alias, "curl -sS --max-time 5 https://pypi.org/simple/", check=False).returncode != 0
    assert main(["--dir", str(rig), "infra", "verify", "dev"]) == 0
    assert main(["--dir", str(rig), "infra", "verify", "k8s"]) == 0
    for name, port in (("dev", 8000), ("k8s", 30080)):
        ip = state["infras"][name]["ip"]
        for _ in range(60):
            out = sh("curl", "-fsS", "--max-time", "10", f"http://{ip}:{port}/healthz", check=False).stdout
            if out and json.loads(out).get("sealed_now"):
                break
            time.sleep(5)
        assert out and json.loads(out)["sealed_now"], f"{name} did not come back sealed: {out}"
    assert main(["--dir", str(rig), "report"]) == 0
    assert "Sealed and verified" in (rig / "state" / "report.html").read_text()


def test_11_dashboard_endpoints_against_the_rig(rig):
    """The dashboard's server-side pieces, against real state and real machines."""
    from outpost.paths import Platform
    from outpost.ui import server
    platform = Platform(rig)
    data = server.overview(platform)
    assert data["report"]["ok"] and set(data["hosts"]) >= {"lab-gitea", "lab-node-1"} and data["drift"] == {}
    fresh = server.refresh_hosts(platform, ["lab-node-1"])
    assert fresh["lab-node-1"]["workloads"]["compose_projects"] == ["demo-dev"]
