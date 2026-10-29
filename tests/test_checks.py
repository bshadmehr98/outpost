from pathlib import Path

from outpost.checks import load_checks, run_check
from outpost.ssh import Result
from tests.conftest import FakeHost

TEMPLATE_CHECKS = Path(__file__).resolve().parents[1] / "outpost" / "templates" / "workspace" / "checks"


def test_load_template_checks():
    checks = load_checks(TEMPLATE_CHECKS)
    names = {c.name: c for c in checks}
    assert set(names) == {"hello-python", "hello-k3s", "hello-alpine", "hello-proxy", "hello-proxy-k3s"}
    assert names["hello-proxy"].requires == ["proxy"] and names["hello-python"].requires == []
    hello = names["hello-python"]
    assert (
        hello.context == TEMPLATE_CHECKS / "hello-python"
        and hello.applies_to("compose")
        and not hello.applies_to("k3s")
    )
    assert hello.steps[1]["expect"] == "Hello from Outpost"
    assert names["hello-k3s"].context is None
    alpine = names["hello-alpine"]
    assert alpine.context is not None
    assert (
        alpine.context == TEMPLATE_CHECKS / "hello-alpine"
        and alpine.applies_to("swarm")
        and not alpine.applies_to("k3s")
    )
    assert (
        alpine.steps[1]["expect"] == "curl "
        and "apk add --no-cache curl" in (alpine.context / "Dockerfile").read_text()
    )
    assert "library/alpine:3.20" in (alpine.context / "Dockerfile").read_text()


def hello_python():
    return next(c for c in load_checks(TEMPLATE_CHECKS) if c.name == "hello-python")


def test_run_check_passes_and_copies_context():
    host = FakeHost(
        responses=[
            (r"docker run --rm outpost-check-hello$", "Hello from Outpost (flask 3)"),
            (r"LEAK \|\| echo SEALED", "SEALED"),
        ]
    )
    check = hello_python()
    result = run_check(check, host, {"GITEA": "gitea.internal", "ORG": "pkgs"}, log=lambda *_: None)
    assert result["ok"] and len(result["steps"]) == 3
    assert host.puts and host.puts[0][1].endswith("/hello-python/context.tgz")
    assert any("export GITEA=gitea.internal ORG=pkgs" in s for s in host.scripts)
    assert host.scripts[-1].startswith("rm -rf /tmp/outpost-checks/hello-python")


def test_run_check_fails_on_expect_and_stops():
    host = FakeHost(responses=[(r"docker run --rm outpost-check-hello$", "something else")])
    check = hello_python()
    result = run_check(check, host, {"GITEA": "g", "ORG": "o"}, log=lambda *_: None)
    assert not result["ok"]
    assert [s["ok"] for s in result["steps"]] == [True, False]


def test_run_check_fails_on_exit_code():
    host = FakeHost(responses=[(r"docker build", Result(1, "", "boom"))])
    check = hello_python()
    result = run_check(check, host, {"GITEA": "g", "ORG": "o"}, log=lambda *_: None)
    assert not result["ok"] and result["steps"][0]["output"] == "boom"
