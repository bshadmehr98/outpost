"""Smoke checks: small scenarios that prove an infrastructure really builds and runs offline.

    checks/hello-python.yml
    checks/hello-python/Dockerfile        optional folder with the same name, copied to the node

    name: hello-python
    description: Build an image with apt + pip from Gitea, run it, confirm the internet is blocked.
    infra: [compose, swarm]              # which infrastructure types this applies to (default: all)
    requires: [proxy]                    # optional: skipped, with a message, unless the egress proxy is set up
    steps:
      - run: docker build --build-arg GITEA=$GITEA --build-arg ORG=$ORG -t check-hello .
      - run: docker run --rm check-hello
        expect: Hello from Outpost

Steps run on the first node, in the copied folder, as root, with $GITEA and $ORG set, and with an egress
proxy also $PROXY_URL, $PROXY_ALLOWED (a name it lets through) and $PROXY_REFUSED (one it must refuse).
A step passes when its command exits 0 and, if `expect:` is given, its output contains that text.

    found = checks_for(system, system.infra("dev"), only=None)
    results = run_checks(system, system.infra("dev"), found, log=print)    # also state/check-dev.json

    check.py     Check, REQUIREMENTS
    load.py      load_checks()
    run.py       checks_for(), run_checks(), run_check(), proxy_check_env()

Code outside this folder uses only the names below.
"""

from outpost.checks.check import REQUIREMENTS, Check
from outpost.checks.load import load_checks
from outpost.checks.run import checks_for, proxy_check_env, run_check, run_checks

__all__ = ["REQUIREMENTS", "Check", "checks_for", "load_checks", "proxy_check_env", "run_check", "run_checks"]
