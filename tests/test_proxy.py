"""The egress proxy: servers.yml, the tinyproxy files, the seal's one exception, the client settings,
the setup order on a fake host, the needs injection, the CLI, the checks and the report."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from outpost import hosts, report
from outpost.checks import load_checks
from outpost.cli import build_parser, cmd_proxy, main
from outpost.config import ConfigError, ProxyConfig, load_servers
from outpost.infra import clients, common, egress, k3s, proxy, verify
from outpost.infra.clients import GiteaInfo
from outpost.infra.common import NodeContext
from outpost.needs import Needs
from outpost.paths import Platform
from outpost.ssh import Result
from tests.conftest import FakeHost

G = GiteaInfo(name="gitea.internal", ip="10.0.0.5", org="pkgs")
CFG = ProxyConfig(host="gitea-1", allow=["api.twilio.com", "*.sendgrid.net"])
ON_GITEA = proxy.info(CFG, G, "gitea-1")
ELSEWHERE = proxy.info(ProxyConfig(host="proxy-1", allow=["api.twilio.com"]), G, "gitea-1", ip="10.0.0.40")
NO_PROXY = "gitea.internal,10.0.0.5,localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def servers_yml(tmp_path: Path, proxy_block: str) -> Path:
    path = tmp_path / "servers.yml"
    path.write_text("gitea: {host: gitea-1, name: gitea.internal, ip: 10.0.0.5}\n"
                    "infrastructures:\n  dev: {type: compose, nodes: [dev-1]}\n" + proxy_block)
    return path


# -- servers.yml --------------------------------------------------------------------

def test_config_parses_and_defaults_to_the_gitea_host(tmp_path):
    servers = load_servers(servers_yml(tmp_path, "egress_proxy:\n  allow: [API.Twilio.com, '*.sendgrid.net', api.twilio.com]\n"))
    p = servers.egress_proxy
    assert p.host == "gitea-1" and p.port == 3128 and p.name == "proxy.internal" and p.distro is None
    assert p.allow == ["api.twilio.com", "*.sendgrid.net"]   # lower-cased, duplicates dropped
    other = load_servers(servers_yml(tmp_path, "egress_proxy: {host: proxy-1, port: 8888, allow: api.twilio.com, distro: Rocky9}\n")).egress_proxy
    assert (other.host, other.port, other.allow, other.distro) == ("proxy-1", 8888, ["api.twilio.com"], "rocky9")
    assert load_servers(servers_yml(tmp_path, "")).egress_proxy is None


@pytest.mark.parametrize("block,message", [
    ("egress_proxy: {allow: [10.1.2.3]}", "is an address"),
    ("egress_proxy: {allow: ['https://api.twilio.com']}", "without scheme"),
    ("egress_proxy: {allow: [api.twilio.com/v1]}", "without scheme, port or path"),
    ("egress_proxy: {allow: ['api.twilio.com:443']}", "without scheme, port or path"),
    ("egress_proxy: {allow: ['*.com']}", "whole top-level domain"),
    ("egress_proxy: {allow: [localhost]}", "not a host name"),
    ("egress_proxy: {allow: []}", "at least one host name"),
    ("egress_proxy: {}", "at least one host name"),
    ("egress_proxy: {allow: [a.example.com], port: 70000}", "from 1 to 65535"),
    ("egress_proxy: {allow: [a.example.com], port: 'x'}", "from 1 to 65535"),
    ("egress_proxy: {allow: [a.example.com], prot: 1}", "unknown keys: prot"),
    ("egress_proxy: [a.example.com]", "must be a mapping"),
])
def test_config_rejects(tmp_path, block, message):
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_servers(servers_yml(tmp_path, block + "\n"))


# -- where nodes find it --------------------------------------------------------------

def test_info_on_the_gitea_host_and_elsewhere():
    assert ON_GITEA.url == "http://gitea.internal:3128" and ON_GITEA.on_gitea and ON_GITEA.ip == "10.0.0.5"
    assert ON_GITEA.no_proxy == NO_PROXY
    assert ELSEWHERE.url == "http://proxy.internal:3128" and not ELSEWHERE.on_gitea
    assert ELSEWHERE.no_proxy == NO_PROXY + ",proxy.internal,10.0.0.40"
    with pytest.raises(RuntimeError, match="no known address"):
        proxy.info(ProxyConfig(host="proxy-1", allow=["a.example.com"]), G, "gitea-1")
    ctx = NodeContext(gitea=G, ca_pem="CA", proxy=ELSEWHERE)
    assert ctx.hosts_entries() == [("10.0.0.5", "gitea.internal"), ("10.0.0.40", "proxy.internal")]
    assert ctx.proxy_user_for("proxy-1") == "outpost-proxy" and ctx.proxy_user_for("dev-1") is None
    assert NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA).hosts_entries() == [("10.0.0.5", "gitea.internal")]


# -- tinyproxy's files ----------------------------------------------------------------

def test_allow_file_is_anchored_and_wildcards_cover_the_domain():
    text = proxy.allow_file(CFG.allow, "gitea.internal")
    rules = [line for line in text.splitlines() if not line.startswith("#")]
    assert rules == [r"^gitea\.internal$", r"^api\.twilio\.com$", r"(^|\.)sendgrid\.net$"]
    # what tinyproxy does with them: extended regex, case-insensitive, against the bare host name
    match = lambda name: any(re.search(r, name, re.I) for r in rules)   # noqa: E731
    assert match("api.twilio.com") and match("API.Twilio.COM") and match("sendgrid.net") and match("eu.api.sendgrid.net")
    assert not match("api.twilio.com.evil.org") and not match("xapi.twilio.com") and not match("evilsendgrid.net")
    assert not match("example.com")
    assert proxy.allowed_by(CFG.allow, "x.sendgrid.net") and not proxy.allowed_by(CFG.allow, "example.com")
    assert proxy.probe_name(CFG.allow) == "api.twilio.com" and proxy.probe_name(["*.sendgrid.net"]) == "sendgrid.net"
    assert proxy.refused_name(CFG.allow) == "example.com" and proxy.refused_name(["example.com"]) == "example.net"


def test_conf_uses_documented_directives_only():
    conf = proxy.conf("10.0.0.5", 3128, ["203.0.113.9", "198.51.100.0/24", "not-an-address"], "1.11.1")
    lines = [line for line in conf.splitlines() if line and not line.startswith("#")]
    directives = {line.split()[0] for line in lines}
    assert directives == {"User", "Group", "Port", "Listen", "Timeout", "MaxClients", "LogFile", "LogLevel", "Allow",
                          "ConnectPort", "Filter", "FilterURLs", "FilterType", "FilterDefaultDeny", "DisableViaHeader"}
    assert "Listen 10.0.0.5" in lines and "Port 3128" in lines and "User outpost-proxy" in lines
    assert 'LogFile "/var/log/tinyproxy/tinyproxy.log"' in lines and "LogLevel Connect" in lines
    assert 'Filter "/usr/share/tinyproxy/outpost.allow"' in lines and "FilterURLs Off" in lines and "FilterDefaultDeny Yes" in lines
    assert "FilterType ere" in lines and ["ConnectPort 443", "ConnectPort 80"] == [x for x in lines if x.startswith("ConnectPort")]
    assert [x for x in lines if x.startswith("Allow")] == ["Allow 127.0.0.1", "Allow 10.0.0.0/8", "Allow 172.16.0.0/12",
                                                           "Allow 192.168.0.0/16", "Allow 203.0.113.9", "Allow 198.51.100.0/24"]
    # before 1.11.1 there is no FilterType, and an unknown directive stops tinyproxy from starting
    assert "FilterExtended On" in proxy.conf("10.0.0.5", 3128, [], "1.11.0") and "FilterType" not in proxy.conf("10.0.0.5", 3128, [], "1.10.0")


def test_unit_runs_tinyproxy_in_the_foreground_as_its_user():
    unit = proxy.unit("/usr/bin/tinyproxy")
    assert "ExecStart=/usr/bin/tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf" in unit
    assert "User=outpost-proxy" in unit and "Restart=always" in unit and "WantedBy=multi-user.target" in unit
    # as root before every start: the package's postinst hands the log directory back to its own user on upgrade
    assert f"ExecStartPre=+/bin/sh -c '{proxy.LOG_PREP}'" in unit and "'" not in proxy.LOG_PREP and "$" not in proxy.LOG_PREP


def test_files_are_where_an_apparmor_tinyproxy_profile_allows():
    """The stock profile `tinyproxy` lets /usr/bin/tinyproxy read /etc/tinyproxy/tinyproxy.conf and
    /usr/share/tinyproxy/*, and write /var/log/tinyproxy/tinyproxy.log; any other path is denied, even to root."""
    assert proxy.CONF_PATH == "/etc/tinyproxy/tinyproxy.conf"
    assert proxy.ALLOW_PATH.startswith("/usr/share/tinyproxy/") and proxy.ALLOW_PATH.count("/") == 4
    assert proxy.LOG_PATH == "/var/log/tinyproxy/tinyproxy.log" and proxy.LOGROTATE.startswith(proxy.LOG_PATH + " {")
    assert "copytruncate" in proxy.LOGROTATE and "postrotate" not in proxy.LOGROTATE   # the stock unit is masked, nothing to reload


# -- the seal's one exception ---------------------------------------------------------

def run_seal(tmp_path: Path, script: str, user_exists: bool = True) -> list[str]:
    """Run the seal with fake iptables, ip6tables and id on PATH; returns every iptables call in order."""
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    for tool in ("iptables", "ip6tables"):
        (fake / tool).write_text(f'#!/bin/bash\necho "{tool} $*" >> {calls}\ncase " $* " in *" -C "*) exit 1;; esac\nexit 0\n')
        (fake / tool).chmod(0o755)
    (fake / "id").write_text(f"#!/bin/bash\nexit {0 if user_exists else 1}\n")
    (fake / "id").chmod(0o755)
    path = tmp_path / "outpost-egress"
    path.write_text(script.replace("LOG=/var/log/outpost-egress.log", f"LOG={tmp_path}/egress.log"))
    out = subprocess.run(["bash", str(path), "seal", "test"], capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{fake}:/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    return calls.read_text().splitlines()


def test_seal_without_a_proxy_is_unchanged(tmp_path):
    script = egress.script("10.0.0.5", ["203.0.113.9"])
    assert "owner" not in script and "OUTPOST-OUT" not in script and "PROXY_USER" not in script
    calls = run_seal(tmp_path, script)
    assert "iptables -t filter -I OUTPUT 1 -j OUTPOST" in calls and "iptables -t security -I OUTPUT 1 -j OUTPOST" in calls
    assert not any("owner" in c for c in calls)


def test_seal_on_the_proxy_host_exempts_its_user_in_both_tables(tmp_path):
    script = egress.script("10.0.0.5", [], proxy_user="outpost-proxy")
    calls = [c for c in run_seal(tmp_path, script) if c.startswith("iptables ")]
    for table, last in (("filter", "-j REJECT --reject-with icmp-admin-prohibited"), ("security", "-j DROP")):
        out_chain = [c for c in calls if f"-t {table} -A OUTPOST-OUT" in c]
        assert out_chain == [
            f"iptables -t {table} -A OUTPOST-OUT -m owner --uid-owner outpost-proxy -p tcp --dport 443 -j RETURN",
            f"iptables -t {table} -A OUTPOST-OUT -m owner --uid-owner outpost-proxy -p tcp --dport 80 -j RETURN",
            f"iptables -t {table} -A OUTPOST-OUT -j OUTPOST",
        ]
        # OUTPOST, which FORWARD also jumps to, never holds an owner match and still ends in REJECT or DROP
        chain = [c for c in calls if f"-t {table} -A OUTPOST " in c]
        assert chain[-1].endswith(last) and not any("owner" in c for c in chain)
        hooks = [c for c in calls if f"-t {table} -I " in c]
        assert f"iptables -t {table} -I OUTPUT 1 -j OUTPOST-OUT" in hooks and f"iptables -t {table} -I FORWARD 1 -j OUTPOST" in hooks
        assert f"iptables -t {table} -I OUTPUT 1 -j OUTPOST" not in hooks
        # the exception exists before anything is hooked: OUTPOST-OUT is complete when OUTPUT first jumps to it
        assert calls.index(out_chain[-1]) < calls.index(f"iptables -t {table} -I OUTPUT 1 -j OUTPOST-OUT")
    assert not any("owner" in c for c in run_seal(tmp_path, script) if c.startswith("ip6tables "))
    # without the user (a boot before `proxy setup` made it), the rule is left out rather than failing the seal
    (tmp_path / "calls").unlink()
    assert not any("owner" in c for c in run_seal(tmp_path, script, user_exists=False))


def test_installing_the_plain_seal_removes_an_old_exception():
    host = FakeHost("gitea-1")
    egress.install(host, "10.0.0.5", [])
    assert "OUTPOST-OUT" not in host.files[egress.SCRIPT_PATH] and host.ran("-D OUTPUT -j OUTPOST-OUT")
    host = FakeHost("gitea-1")
    egress.install(host, "10.0.0.5", [], proxy_user="outpost-proxy")
    assert "--uid-owner" in host.files[egress.SCRIPT_PATH] and not host.ran("-D OUTPUT -j OUTPOST-OUT")


# -- what the nodes get ---------------------------------------------------------------

def test_client_files_with_and_without_a_proxy():
    plain = {**clients.client_files(G, "CA", None, []), **clients.host_files(G, "CA", "10.0.0.11")}
    assert clients.PATH_PROXY_ENV not in plain and "PROXY" not in plain[clients.PATH_PROFILE]
    assert plain[clients.PATH_PROFILE] == ("export PIP_INDEX_URL=https://gitea.internal/api/packages/pkgs/pypi/simple\nexport PIP_CERT=/etc/outpost/ca.crt\n"
                                           "export NPM_CONFIG_REGISTRY=https://gitea.internal/api/packages/pkgs/npm/\nexport NPM_CONFIG_CAFILE=/etc/outpost/ca.crt\n")
    assert not any("proxy" in p for p in clients.config_image_files(G, "CA", None, [])) \
        and clients.PATH_PROFILE not in clients.config_image_files(G, "CA", None, [])
    files = {**clients.client_files(G, "CA", None, [], proxy=ON_GITEA), **clients.host_files(G, "CA", "10.0.0.11", proxy=ON_GITEA)}
    env = dict(line.split("=", 1) for line in files[clients.PATH_PROXY_ENV].splitlines() if line and not line.startswith("#"))
    assert env == {"HTTPS_PROXY": "http://gitea.internal:3128", "HTTP_PROXY": "http://gitea.internal:3128", "NO_PROXY": NO_PROXY,
                   "https_proxy": "http://gitea.internal:3128", "http_proxy": "http://gitea.internal:3128", "no_proxy": NO_PROXY,
                   "OUTPOST_PROXY_ALLOWED": "api.twilio.com,*.sendgrid.net"}
    assert "export HTTPS_PROXY=http://gitea.internal:3128\n" in files[clients.PATH_PROFILE] and "export PIP_CERT" in files[clients.PATH_PROFILE]
    image = clients.config_image_files(G, "CA", None, [], proxy=ON_GITEA)
    assert image[clients.PATH_PROXY_ENV][0].decode() == files[clients.PATH_PROXY_ENV]
    assert image[clients.PATH_PROFILE][0].decode().startswith("export HTTPS_PROXY=http://gitea.internal:3128\n")


def test_docker_client_config_is_merged_and_only_ours_is_removed():
    existing = json.dumps({"auths": {"gitea.internal": {"auth": "eA=="}}})
    merged = json.loads(clients.docker_client_config(existing, ON_GITEA, G.name))
    assert merged["auths"] == {"gitea.internal": {"auth": "eA=="}}
    assert merged["proxies"] == {"default": {"httpProxy": "http://gitea.internal:3128", "httpsProxy": "http://gitea.internal:3128", "noProxy": NO_PROXY}}
    assert clients.docker_client_config(json.dumps(merged), ON_GITEA, G.name) is None          # nothing to change
    assert clients.docker_client_config("", None, G.name) is None                              # no proxy, no file: nothing written
    assert clients.docker_client_config("not json", ON_GITEA, G.name) is None                  # never clobber what we cannot read
    removed = json.loads(clients.docker_client_config(json.dumps(merged), None, G.name))
    assert removed == {"auths": {"gitea.internal": {"auth": "eA=="}}}
    theirs = json.dumps({"proxies": {"default": {"httpsProxy": "http://corp:8080", "noProxy": "corp.local"}}})
    assert clients.docker_client_config(theirs, None, G.name) is None


def test_environment_lines_are_replaced_in_place():
    script = clients.environment_script(ON_GITEA)
    assert "echo HTTPS_PROXY=http://gitea.internal:3128;" in script and "> /etc/environment.outpost && cat /etc/environment.outpost > /etc/environment" in script
    assert clients.environment_script(None).startswith("if grep -qs '^# Outpost egress proxy' /etc/environment")


def test_prepare_node_without_a_proxy_writes_no_proxy_settings():
    host = FakeHost("dev-1")
    common.prepare_node(host, NodeContext(gitea=G, ca_pem="CA", infra_name="dev"), log=lambda *_: None)
    assert host.files[clients.PATH_PROXY_ENV] == clients.NO_PROXY_ENV and "PROXY" not in host.files[clients.PATH_PROFILE]
    assert not any(p.endswith(".docker/config.json") for p in host.files)
    assert "OUTPOST-OUT" not in host.files[egress.SCRIPT_PATH] and "proxy" not in host.files[egress.SCRIPT_PATH]
    assert not host.ran("Outpost egress proxy'; echo")   # /etc/environment untouched


def test_prepare_node_with_a_proxy_elsewhere():
    host = FakeHost("dev-1")
    ctx = NodeContext(gitea=G, ca_pem="CA", allow=["10.0.0.40"], infra_name="dev", proxy=ELSEWHERE)
    common.prepare_node(host, ctx, log=lambda *_: None)
    assert host.ran("echo '10.0.0.40 proxy.internal'") and "10.0.0.40 proxy.internal" in host.files["/usr/local/sbin/outpost-hosts"]
    assert "HTTPS_PROXY=http://proxy.internal:3128" in host.files[clients.PATH_PROXY_ENV]
    for home in ("/root", common.RUNNER_HOME):
        assert json.loads(host.files[f"{home}/.docker/config.json"])["proxies"]["default"]["httpsProxy"] == "http://proxy.internal:3128"
    assert "10.0.0.40" in host.files[egress.SCRIPT_PATH] and "owner" not in host.files[egress.SCRIPT_PATH]   # not the proxy host
    assert not any("docker.service.d" in p for p in host.files) and "proxies" not in host.files["/etc/docker/daemon.json"]


def test_configmap_and_verify_lines():
    doc = yaml.safe_load(k3s.configmap_yaml(ON_GITEA))
    assert doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "outpost-proxy" and doc["metadata"]["namespace"] == "default"
    assert doc["data"]["HTTPS_PROXY"] == "http://gitea.internal:3128" and doc["data"]["NO_PROXY"] == NO_PROXY
    assert doc["data"]["OUTPOST_PROXY_ALLOWED"] == "api.twilio.com,*.sendgrid.net"
    server = FakeHost("k3s-1")
    k3s.apply_configmap(server, ON_GITEA, log=lambda *_: None)
    assert server.ran("k3s kubectl apply -f -") and server.ran("name: outpost-proxy")
    k3s.apply_configmap(server, None, log=lambda *_: None)
    assert server.ran("delete configmap outpost-proxy --ignore-not-found")
    assert verify.proxy_checks(NodeContext(gitea=G, ca_pem="CA")) == []
    checks = dict(verify.proxy_checks(NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA)))
    assert set(checks) == {"proxy settings for programs that opt in", "proxy reachable", "proxy refuses unknown names"}
    assert "curl -x http://gitea.internal:3128" in checks["proxy reachable"] and "https://api.twilio.com/" in checks["proxy reachable"]
    assert "%{http_connect}" in checks["proxy refuses unknown names"] and "https://example.com/" in checks["proxy refuses unknown names"]
    assert '= 403' in checks["proxy refuses unknown names"]
    # the direct path is tested without the proxy variables a node's ssh session may carry
    assert "--noproxy '*'" in dict(verify.common_checks(NodeContext(gitea=G, ca_pem="CA")))["public internet rejected"]


# -- setup on a fake host -------------------------------------------------------------

class OrderedHost(FakeHost):
    """A FakeHost that also keeps one ordered list of what happened."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.events: list[str] = []

    def run(self, script, check=True, timeout=0):
        self.events.append("run: " + script)
        return super().run(script, check, timeout)

    def write(self, path, content, mode="0644"):
        self.events.append("write: " + path)
        return super().write(path, content, mode)

    def first(self, text: str) -> int:
        return next(i for i, e in enumerate(self.events) if text in e)


INSTALLED = [(r"command -v tinyproxy", "/usr/bin/tinyproxy\ntinyproxy 1.11.1\n")]


def test_setup_on_the_gitea_host_order(monkeypatch):
    monkeypatch.setattr(common, "prepare_node", lambda *a, **k: pytest.fail("the Gitea host is not prepared as a node"))
    host = OrderedHost("gitea-1", facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5"}, responses=INSTALLED)
    ctx = NodeContext(gitea=G, ca_pem="CA", distros=["bookworm", "noble"], proxy=ON_GITEA)
    result = proxy.setup(host, ctx, CFG, egress_allow=[], client_allow=["203.0.113.9"], log=lambda *_: None)
    assert result["tinyproxy"] == "1.11.1" and result["ip"] == "10.0.0.5" and result["on_gitea"] and result["distro"] == "noble"
    source = host.first("sources.list.d/outpost.list")
    package = host.first("apt-get install -y")
    user = host.first("useradd --system")
    conf = host.first("write: /etc/tinyproxy/tinyproxy.conf")
    unit = host.first("write: /etc/systemd/system/outpost-proxy.service")
    egress_script = host.first(f"write: {egress.SCRIPT_PATH}")
    seal = host.first(f"{egress.SCRIPT_PATH} seal")
    assert source < package < user < conf < unit < egress_script < seal == len(host.events) - 1
    install = host.events[package]
    assert install.index("systemctl mask tinyproxy.service") < install.index("apt-get install -y")   # the stock unit never starts
    assert "Listen 10.0.0.5" in host.files[proxy.CONF_PATH] and "FilterType ere" in host.files[proxy.CONF_PATH]
    assert "--uid-owner" in host.files[egress.SCRIPT_PATH] and "Acquire::https::CaInfo" in host.files["/etc/apt/apt.conf.d/99outpost"]
    assert host.files[proxy.ALLOW_PATH] == proxy.allow_file(CFG.allow, G.name)


def test_setup_on_another_host_prepares_it_first(monkeypatch):
    seen = []
    monkeypatch.setattr(common, "prepare_node", lambda host, ctx, log=print: seen.append(len(host.events)) or {})
    host = OrderedHost("proxy-1", facts={"id": "rocky", "id_like": "rhel centos fedora", "version_id": "9.4", "arch": "x86_64", "ip": "10.0.0.40"},
                       responses=INSTALLED)
    cfg = ProxyConfig(host="proxy-1", allow=["api.twilio.com"])
    ctx = NodeContext(gitea=G, ca_pem="CA", proxy=ELSEWHERE)
    result = proxy.setup(host, ctx, cfg, egress_allow=[], client_allow=[], log=lambda *_: None)
    assert seen and seen[0] <= host.first("dnf install -y -q tinyproxy")
    assert result["family"] == "rpm" and result["distro"] == "rocky9" and not result["on_gitea"]
    assert "Listen 10.0.0.40" in host.files[proxy.CONF_PATH]


def test_setup_explains_a_missing_package_and_refuses_alpine():
    host = FakeHost("gitea-1", facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64"}, responses=[(r"apt-cache show", "OUTPOST-NO-TINYPROXY\n")])
    with pytest.raises(RuntimeError, match=r"no tinyproxy for noble\. Run `outpost fetch --only apt` and `outpost upload`"):
        proxy.setup(host, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG, egress_allow=[], client_allow=[], log=lambda *_: None)
    alpine = FakeHost("gitea-1", facts={"id": "alpine", "version_id": "3.20.3", "arch": "x86_64"})
    with pytest.raises(RuntimeError, match="Alpine"):
        proxy.setup(alpine, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG, egress_allow=[], client_allow=[], log=lambda *_: None)


def test_missing_package_error_shows_what_the_package_manager_saw():
    seen = ("--- apt-get update\nErr:1 https://gitea.internal noble InRelease\n"
            "--- /etc/apt/sources.list.d/outpost.list\ndeb [signed-by=/etc/outpost/gitea-apt.asc] x bookworm main\n"
            "--- apt-cache policy tinyproxy\nN: Unable to locate package tinyproxy\n")
    host = FakeHost("gitea-1", facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64"},
                    responses=[(r"apt-cache show", "OUTPOST-NO-TINYPROXY\n"), (r"--- apt-get update", seen)])
    with pytest.raises(RuntimeError) as err:
        proxy.setup(host, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG, egress_allow=[], client_allow=[], log=lambda *_: None)
    assert "Err:1 https://gitea.internal noble InRelease" in str(err.value) and "Unable to locate package" in str(err.value)
    script = host.scripts[-1]
    assert script.startswith("set +e +o pipefail\n")          # a failing probe (ls of a missing file) must not cut the report short
    assert "apt-get update 2>&1" in script and "cat /etc/apt/sources.list.d/outpost.list" in script
    assert "apt-cache policy tinyproxy" in script and f"{G.apt_repo}/dists/noble/Release" in script
    rpm = proxy.diagnose_script("rpm", G, "rocky9")
    assert "dnf makecache" in rpm and f"cat {clients.PATH_RPM_REPO}" in rpm and f"{G.rpm_repo}/rocky9/repodata/repomd.xml" in rpm
    assert "dnf info tinyproxy" in rpm


def test_unreadable_configuration_and_failed_start_say_why():
    facts = {"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5"}
    report = "--- namei -l /etc/tinyproxy/tinyproxy.conf\n drwx------ root root tinyproxy\n"
    host = FakeHost("gitea-1", facts=facts, responses=INSTALLED + [(r"--- namei", report), (r"test -r /etc/tinyproxy/tinyproxy.conf", Result(1, "", ""))])
    with pytest.raises(RuntimeError, match=r"outpost-proxy cannot read its configuration or write its log:\n--- namei -l /etc/tinyproxy/tinyproxy.conf\n drwx------"):
        proxy.setup(host, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG, egress_allow=[], client_allow=[], log=lambda *_: None)
    prep = next(s for s in host.scripts if "useradd --system" in s)
    assert proxy.LOG_PREP in prep and "chown outpost-proxy:outpost-proxy /var/log/tinyproxy/tinyproxy.log" in prep
    assert "rm -f /etc/outpost/proxy.conf /etc/outpost/proxy.allow /etc/logrotate.d/outpost-proxy" in prep   # an earlier setup's files
    assert any("test -w /var/log/tinyproxy/tinyproxy.log" in s for s in host.scripts)

    host = FakeHost("gitea-1", facts=facts, responses=INSTALLED + [(r"--- namei", report + "--- /usr/bin/tinyproxy -d -c\nexit 124\n"),
                                                              (r"systemctl restart outpost-proxy", Result(3, "failed", ""))])
    with pytest.raises(RuntimeError, match=r"did not start:(.|\n)*exit 124"):
        proxy.setup(host, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG, egress_allow=[], client_allow=[], log=lambda *_: None)
    script = proxy.access_script("/usr/bin/tinyproxy")
    assert script.startswith("set +e +o pipefail\n") and "namei -l /etc/tinyproxy/tinyproxy.conf" in script
    assert "timeout 2 runuser -u outpost-proxy -- /usr/bin/tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf" in script
    assert "/sys/kernel/security/apparmor/profiles" in script and "-c /tmp/outpost-proxy-copy.conf" in script
    assert "tinyproxy -d" not in proxy.access_script()


def test_host_checks_cover_the_exception_and_the_log():
    names = [n for n, _ in proxy.host_checks(NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG)]
    assert names[:3] == ["proxy service active", "tinyproxy installed from gitea", "proxy allow list is the current one"]
    assert "proxy exception only for its own user" in names and "public internet rejected for other users" in names
    host = FakeHost("gitea-1", responses=[(r"CHECK\|", "CHECK|proxy service active|OK|active\n")])
    assert proxy.verify(host, NodeContext(gitea=G, ca_pem="CA", proxy=ON_GITEA), CFG) == [{"name": "proxy service active", "ok": True, "detail": "active"}]


# -- tinyproxy in the box -------------------------------------------------------------

def test_needs_get_tinyproxy_for_the_proxy_hosts_distro():
    needs = proxy.add_package(Needs(apt={"noble": ["curl"]}), ProxyConfig(host="g", allow=["a.example.com"], distro="noble"), None)
    assert needs.apt == {"noble": ["curl", "tinyproxy"]}
    assert proxy.add_package(Needs(), ProxyConfig(host="g", allow=["a.example.com"], distro="rocky9"), None).rpm == {"rocky9": ["tinyproxy"]}
    cfg = ProxyConfig(host="g", allow=["a.example.com"])
    assert proxy.add_package(Needs(), cfg, {"id": "debian", "codename": "bookworm"}).apt == {"bookworm": ["tinyproxy"]}
    assert proxy.add_package(Needs(), cfg, {"id": "almalinux", "id_like": "rhel centos fedora", "version_id": "9.4"}).rpm == {"alma9": ["tinyproxy"]}
    assert proxy.add_package(Needs(), None, None).is_empty()
    with pytest.raises(ConfigError, match="outpost hosts --refresh"):
        proxy.add_package(Needs(), cfg, None)
    with pytest.raises(ConfigError, match="Alpine"):
        proxy.add_package(Needs(), cfg, {"id": "alpine", "version_id": "3.20.1"})
    with pytest.raises(ConfigError, match="neither a Debian or Ubuntu codename"):
        proxy.add_package(Needs(), ProxyConfig(host="g", allow=["a.example.com"], distro="windows"), None)


def test_outpost_needs_shows_tinyproxy(platform_dir, capsys):
    path = platform_dir / "servers.yml"
    path.write_text(path.read_text() + "\negress_proxy:\n  allow: [api.twilio.com]\n  distro: noble\n")
    assert main(["--dir", str(platform_dir), "needs"]) == 0
    out = capsys.readouterr().out
    assert "apt    (noble) tinyproxy" in out and "egress proxy on gitea-1" in out
    path.write_text(path.read_text().replace("  distro: noble\n", ""))
    assert main(["--dir", str(platform_dir), "needs"]) == 1
    assert "Set `distro:` under egress_proxy" in capsys.readouterr().err


# -- the CLI and the checks -----------------------------------------------------------

def test_cli_parses_proxy_setup_and_verify(platform_dir, capsys):
    for action in ("setup", "verify"):
        args = build_parser().parse_args(["proxy", action])
        assert args.func is cmd_proxy and args.action == action
    with pytest.raises(SystemExit):
        build_parser().parse_args(["proxy", "open"])
    assert main(["--dir", str(platform_dir), "proxy", "setup"]) == 1
    assert "no egress_proxy section" in capsys.readouterr().err


def test_check_needing_the_proxy_is_skipped_without_one(platform_dir, monkeypatch, capsys):
    import outpost.checks as checksmod
    import outpost.cli as climod
    Platform(platform_dir).write_state("gitea", {"ip": "10.0.0.5"})
    monkeypatch.setattr(climod, "Host", lambda alias: FakeHost(alias))
    monkeypatch.setattr(checksmod, "run_check", lambda *a, **k: pytest.fail("must not run"))
    assert main(["--dir", str(platform_dir), "check", "hello-proxy", "--infra", "dev"]) == 0
    assert "check hello-proxy: skipped, it needs the egress proxy" in capsys.readouterr().out
    with pytest.raises(ConfigError, match="requires"):
        (platform_dir / "checks" / "odd.yml").write_text("steps: [true]\nrequires: [vpn]\n")
        load_checks(platform_dir / "checks")


def test_check_env_comes_from_the_proxy_state(platform_dir, monkeypatch):
    import outpost.checks as checksmod
    import outpost.cli as climod
    path = platform_dir / "servers.yml"
    path.write_text(path.read_text() + "\negress_proxy: {allow: ['*.sendgrid.net', example.org]}\n")
    platform = Platform(platform_dir)
    platform.write_state("gitea", {"ip": "10.0.0.5"})
    platform.write_state("proxy", {"url": "http://gitea.internal:3128", "allow": ["*.sendgrid.net", "example.org"], "host": "gitea-1"})
    seen = []
    monkeypatch.setattr(climod, "Host", lambda alias: FakeHost(alias))
    monkeypatch.setattr(checksmod, "run_check", lambda check, host, env, log=print: seen.append(env) or {"name": check.name, "ok": True, "steps": []})
    assert main(["--dir", str(platform_dir), "check", "hello-proxy", "--infra", "dev"]) == 0
    assert seen[0]["PROXY_URL"] == "http://gitea.internal:3128" and seen[0]["PROXY_ALLOWED"] == "example.org" and seen[0]["PROXY_REFUSED"] == "example.com"


# -- day two --------------------------------------------------------------------------

def test_inspect_reads_tinyproxy_and_the_log_tail(tmp_path):
    import base64
    tail = 'NOTICE    Oct 28 10:00:01 [42]: Proxying refused on filtered domain "example.com"\nCONNECT   Oct 28 10:00:02 [42]: Request (file descriptor 7): CONNECT api.twilio.com:443 HTTP/1.1\n'
    facts = f"id=ubuntu\nip=10.0.0.5\negress=sealed\ntinyproxy=1.11.1\nsvc_outpost-proxy=active\nproxy_log_tail={base64.b64encode(tail.encode()).decode()}\n"
    data = hosts.inspect(FakeHost(responses=[(r"egress_boot", facts)]))
    assert data["installed"]["tinyproxy"] == "1.11.1" and data["services"]["outpost-proxy"] == "active"
    assert data["proxy_log_tail"][1].endswith("CONNECT api.twilio.com:443 HTTP/1.1") and hosts.drift(data) == []
    assert "proxy_log_tail=" in hosts.SCRIPT and "if [ -f /var/log/tinyproxy/tinyproxy.log ]" in hosts.SCRIPT
    servers = load_servers(servers_yml(tmp_path, "egress_proxy: {host: proxy-1, allow: [api.twilio.com]}\n"))
    assert hosts.roles(servers)["proxy-1"] == ["egress proxy"]
    on_gitea = load_servers(servers_yml(tmp_path, "egress_proxy: {allow: [api.twilio.com]}\n"))
    assert hosts.roles(on_gitea)["gitea-1"] == ["gitea", "egress proxy"]


def test_report_has_a_named_exceptions_section(platform_dir):
    platform = Platform(platform_dir)
    assert "There is no egress proxy" in report.render(report.build(platform, load_servers(platform.servers_file)))
    path = platform.servers_file
    path.write_text(path.read_text() + "\negress_proxy: {allow: [api.twilio.com]}\n")
    servers = load_servers(path)
    platform.write_state("proxy", {"host": "gitea-1", "ip": "10.0.0.5", "url": "http://gitea.internal:3128", "allow": ["api.twilio.com"],
                                   "tinyproxy": "1.11.1", "when": "2026-10-28T10:00:00",
                                   "verify": {"result": "ok", "checks": [], "when": "2026-10-28T10:01:00"}})
    hosts.record(platform, "gitea-1", {"reachable": True, "egress": "sealed", "installed": {}, "proxy_log_tail": ["CONNECT api.twilio.com:443"]})
    page = report.render(report.build(platform, servers))
    section = page[page.index("<h2>Named exceptions</h2>"):]
    assert "<code>api.twilio.com</code>" in section and "tinyproxy 1.11.1" in section and "2026-10-28T10:00:00" in section
    assert "CONNECT api.twilio.com:443" in section and "all checks passed" in section
    platform.write_state("proxy", {**platform.read_state("proxy"), "verify": {"result": "problems", "checks": [{"name": "proxy reachable", "ok": False}], "when": "x"}})
    assert "egress proxy" in report.verdict(report.build(platform, servers))["problems"]


def test_explain_knows_the_proxy_failures():
    from outpost.ui import explain
    assert explain.explain("error: Gitea's Debian registry has no tinyproxy for noble. Run ...")["title"].startswith("Gitea does not have tinyproxy")
    assert "distro" in explain.explain("error: Outpost does not know which system the egress proxy host gitea-1 runs, so ...")["title"]
    assert explain.explain("error: egress_proxy.allow: 'https://x' must be a host name")["title"].startswith("The egress_proxy section")
    assert explain.explain("    FAIL proxy refuses unknown names  (CONNECT example.com -> 200)")["title"].startswith("The proxy let through")
    assert explain.summarize(["proxy", "setup"], "", 0) == "Set up and verified the egress proxy"


def test_proxy_setup_command_records_state_and_updates_set_up_nodes(platform_dir, monkeypatch, capsys):
    import outpost.cli as climod
    path = platform_dir / "servers.yml"
    path.write_text(path.read_text() + "\negress_proxy: {allow: [api.twilio.com]}\n")
    platform = Platform(platform_dir)
    platform.write_state("gitea", {"ip": "10.0.0.5"})
    platform.write_state("infra-dev", {"type": "compose"})
    (platform_dir / "pki").mkdir(exist_ok=True)
    (platform_dir / "pki" / "ca.crt").write_text("CA")
    made = {}
    checks = "CHECK|proxy service active|OK|active\nCHECK|proxy reachable|OK|api.twilio.com through the proxy -> 200\n"

    def make(alias):
        made[alias] = made.get(alias) or FakeHost(alias, facts={"id": "ubuntu", "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5"},
                                                  responses=INSTALLED + [(r"CHECK\|", checks)])
        return made[alias]
    monkeypatch.setattr(climod, "Host", make)
    assert main(["--dir", str(platform_dir), "proxy", "setup"]) == 0
    state = platform.read_state("proxy")
    assert state["host"] == "gitea-1" and state["ip"] == "10.0.0.5" and state["port"] == 3128 and state["allow"] == ["api.twilio.com"]
    assert state["tinyproxy"] == "1.11.1" and state["when"] and state["verify"]["result"] == "ok"
    assert "HTTPS_PROXY=http://gitea.internal:3128" in made["dev-1"].files[clients.PATH_PROXY_ENV]   # the node set up before
    assert "--uid-owner" in made["gitea-1"].files[egress.SCRIPT_PATH] and egress.SCRIPT_PATH not in made["dev-1"].files
    assert (platform_dir / "state" / "hosts" / "gitea-1.json").exists()
    assert "Next: outpost check hello-proxy" in capsys.readouterr().out


DEMO = Path(__file__).resolve().parents[1] / "outpost" / "templates" / "platform" / "demo"


def test_demo_probes_the_proxy_and_still_deploys_without_one(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("demo_probe", DEMO / "app" / "probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    for var in ("HTTPS_PROXY", "https_proxy", "OUTPOST_PROXY_ALLOWED"):
        monkeypatch.delenv(var, raising=False)
    assert probe.probe_proxy() is None
    monkeypatch.setenv("HTTPS_PROXY", "http://gitea.internal:3128")
    monkeypatch.setenv("OUTPOST_PROXY_ALLOWED", "api.twilio.com,*.sendgrid.net")
    answers = {"api.twilio.com": "reached", "sendgrid.net": "reached", "example.com": "refused"}
    monkeypatch.setattr(probe, "through_proxy", lambda name, proxy: {"target": name, "result": answers[name], "detail": "", "ms": 1})
    out = probe.probe_proxy()
    assert out["ok"] and [r["target"] for r in out["results"]] == ["api.twilio.com", "sendgrid.net", "example.com"]
    answers["example.com"] = "reached"
    assert not probe.probe_proxy()["ok"]
    # the build-time proof is raw TCP: no proxy variable can make it pass through the proxy
    assert "urllib" not in Path(DEMO / "app" / "probe.py").read_text().split("def probe(")[1].split("def probe_all")[0]
    compose = yaml.safe_load((DEMO / "compose.yml").read_text())["services"]["web"]
    assert compose["env_file"] == [{"path": "/etc/outpost/proxy.env", "required": False}]
    assert yaml.safe_load((DEMO / "stack.yml").read_text())["services"]["web"]["env_file"] == "/etc/outpost/proxy.env"
    deployment = next(yaml.safe_load_all((DEMO / "k8s.yml").read_text()))
    assert deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"] == [{"configMapRef": {"name": "outpost-proxy", "optional": True}}]
    assert '"proxy_ok"' in (DEMO / "app" / "app.py").read_text() and "Through the proxy" in (DEMO / "app" / "templates" / "index.html").read_text()
