"""Turn a failed run into a sentence and a fix, and a finished run into one line of history.

    explain(log)                  the first known failure in a log -> {"title", "fix", "line"} or None
    summarize(args, log, exit)    "Uploaded 312 new files to Gitea" / "failed: ..." for the Activity page

Both only read text. The patterns are the errors people hit in the first week; unknown errors
still show the last lines of the log, so nothing is hidden, only translated when we can.
"""

import re

# (regex over the whole log, title, what to do). First match wins, so specific ones come first.
FAILURES = [
    (
        r"Permission denied \(publickey",
        "The server refused the SSH key.",
        "Try `ssh <alias>` in a terminal. The alias in servers.yml must log in without a password; add the key to the server or fix ~/.ssh/config.",
    ),
    (
        r"Could not resolve hostname|Name or service not known",
        "The SSH alias is not known.",
        "The alias in servers.yml must exist in ~/.ssh/config (or the file named by ssh_config:). Check the spelling with `ssh <alias>`.",
    ),
    (
        r"Connection timed out|Operation timed out|No route to host|ssh: connect to host .* port \d+",
        "The server did not answer on SSH.",
        "Is it on, and is port 22 open from this computer? `ssh <alias>` shows the same error; fix that first.",
    ),
    (
        r"sudo: a password is required|sudo: .*password",
        "The SSH user cannot become root without a password.",
        "Outpost needs root or passwordless sudo on every server. Add `<user> ALL=(ALL) NOPASSWD:ALL` to sudoers, or log in as root.",
    ),
    (
        r"could not become root",
        "Connected, but could not become root.",
        "Give the SSH user passwordless sudo on that server (NOPASSWD in sudoers), or use the root account.",
    ),
    (
        r"cannot reach https://\S+: ",
        "A node cannot reach Gitea.",
        "Nodes talk to Gitea on port 443. Check the firewall between them and `gitea.ip` in servers.yml; from the node, `curl -k https://<gitea ip>/api/v1/version` should answer.",
    ),
    (
        r"Gitea did not start within",
        "Gitea did not come up after the install.",
        "The last lines are Gitea's own log. Usual causes: port 443 already in use, or no `git` on the server.",
    ),
    (
        r"Gitea needs git",
        "The Gitea server has no `git` command.",
        "Install it once on that server (apt-get install git) and run Gitea setup again.",
    ),
    (
        r"No space left on device",
        "A disk is full.",
        "Free space where the error happened (this computer for fetch, the server for upload or install) and run again; everything resumes.",
    ),
    (
        r"No matching distribution found for (\S+)",
        "A Python package has no wheel for that version.",
        "pip could not find it on PyPI for the Python version in the needs file. Check the spelling and the Python version; packages without wheels are fetched as sdists only when the spec is exact.",
    ),
    (
        r"apt download for (\w+) failed",
        "Resolving apt packages failed.",
        "The lines above name the package. It must exist in that Debian or Ubuntu release; `apt-cache policy <pkg>` inside `docker run -it debian:<distro>` shows what is available.",
    ),
    (
        r"apk download for (\S+) failed",
        "Resolving Alpine packages failed.",
        "The lines above name the package. It must exist in that Alpine branch; `apk search <pkg>` inside `docker run -it alpine:<version>` shows what is available.",
    ),
    (
        r"rpm download for (\w+) failed",
        "Resolving RPM packages failed.",
        "The lines above name the package. It must exist in that distro's BaseOS, AppStream or extras repository; `dnf search <pkg>` inside `docker run -it rockylinux:9` shows what is available.",
    ),
    (
        r"docker: (command not found|not found)|Cannot connect to the Docker daemon",
        "Docker is not available on this computer.",
        "Fetching apt, apk and rpm packages and the lab need Docker here. Install Docker Desktop or the engine, and make sure your user can run `docker ps`.",
    ),
    (
        r"manifest\.sig does not match",
        "The box signature does not match.",
        "The box was changed after it was packed, or packed with another key. Fetch and pack again, or get the matching pki/box.pub.",
    ),
    (
        r"HTTP 401 from .*(api|v2)",
        "Gitea rejected the API token.",
        "Run Gitea setup again: it notices a token this Gitea does not know and makes a new one.",
    ),
    (
        r"HTTP 413",
        "Gitea refused an upload as too large.",
        "Raise the package size limits in /etc/gitea/app.ini ([packages] LIMIT_SIZE_*) and restart Gitea. Outpost sets them to unlimited on install.",
    ),
    (r"Run `outpost fetch", "The box is empty or incomplete.", "Run Fetch first; the step needs files from the box."),
    (r"Run `outpost gitea setup` first", "Gitea has not been set up yet.", "Run Gitea setup first."),
    (
        r"No infrastructure has been set up yet",
        "No infrastructure is set up yet.",
        "Set up at least one infrastructure before pushing the demo.",
    ),
    (
        r"secrets/gitea-token is missing",
        "The API token is missing.",
        "Run Gitea setup; it creates secrets/gitea-token.",
    ),
    (
        r"another command is still running",
        "Another command is still running.",
        "Wait for it to finish, or stop it from the Activity page.",
    ),
    (
        r"did not start accepting SSH",
        "A lab container did not start.",
        "Run `docker logs <name>`. Usually the machine is out of memory or Docker lacks the privileges the lab needs.",
    ),
    (
        r"registry has no tinyproxy for (\S+)",
        "Gitea does not have tinyproxy for the proxy host yet.",
        "With egress_proxy in servers.yml, fetch adds tinyproxy for the proxy host's distro by itself: run Fetch (`outpost fetch --only apt`, or `--only rpm` on the RHEL family), then Push to Gitea (`outpost upload`), then `outpost proxy setup` again.",
    ),
    (
        r"does not know which system the egress proxy host",
        "Outpost does not know the proxy host's distro.",
        "Set `distro:` under egress_proxy in servers.yml (noble, bookworm, rocky9...), or look at the machines once (`outpost hosts --refresh`) so Outpost can ask the host.",
    ),
    (
        r"does not support as (a|an egress) proxy host",
        "Alpine cannot run the egress proxy.",
        "Put the proxy on a Debian, Ubuntu or RHEL-family host: set egress_proxy.host in servers.yml (it defaults to the Gitea host).",
    ),
    (
        r"no egress_proxy section",
        "servers.yml has no egress proxy.",
        "Add an egress_proxy section with the names to allow (allow: [api.twilio.com]) and run `outpost proxy setup`; see docs/architecture/proxy.md.",
    ),
    (
        r"error: egress_proxy",
        "The egress_proxy section of servers.yml is not valid.",
        "Allow entries are host names (api.twilio.com) or wildcards (*.sendgrid.net): no https://, no port, no path, no IP addresses. The port is a number.",
    ),
    (
        r"outpost-proxy did not start",
        "The egress proxy did not start.",
        'The lines above are its journal. Usual causes: the port in egress_proxy is already taken on that host, or its address is not one of the host\'s own. An `apparmor="DENIED"` line under kernel denials means a local AppArmor profile confines tinyproxy more tightly than the stock one, which allows the paths Outpost uses.',
    ),
    (
        r"FAIL proxy (reachable|user can reach allowed names)",
        "The proxy did not get through to an allowed name.",
        "Check the name is right and answers HTTPS on the internet; on the proxy host, `tail /var/log/tinyproxy/tinyproxy.log` shows the CONNECT and why it failed. `outpost egress <host> status` must say sealed: the seal keeps the proxy's own exception.",
    ),
    (
        r"FAIL proxy refuses unknown names",
        "The proxy let through a name that is not on the allow list.",
        "Run `outpost proxy setup` again: it rewrites /usr/share/tinyproxy/outpost.allow and the configuration that makes the list an allow list (FilterDefaultDeny).",
    ),
    (
        r"hello-k3s.*FAIL|LEAK",
        "A pod could reach the internet.",
        "The seal did not hold for pod traffic. Run Set up again for that infrastructure so the egress script is reinstalled, then Verify.",
    ),
    (
        r"PROBLEMS",
        "Some verification checks failed.",
        "The table above names them. Run Set up again for that infrastructure; every step is safe to repeat.",
    ),
    (
        r"error: (.+)",
        "The command stopped with an error.",
        "The message is the last line of the output. Fix what it names and run again; every command can be repeated safely.",
    ),
]


def explain(log: str) -> dict | None:
    """The first known failure in the last 80 lines of a log, as {"title", "fix", "line"}, or None."""
    tail = "\n".join(log.strip().splitlines()[-80:])
    for pattern, title, fix in FAILURES:
        m = re.search(pattern, tail)
        if m:
            return {"title": title, "fix": fix, "line": m.group(0)[:200]}
    return None


def summarize(args: list[str], log: str, exit_code: int | None) -> str:
    """One line for the Activity page: what the run did, or why it failed.

    Args:
        args: the command line after `outpost`, such as ["infra", "setup", "dev"].
        exit_code: None when the run was cut short.
    """
    if exit_code is None:
        return "was cut short"
    if exit_code != 0:
        failure = explain(log)
        return "failed: " + (failure["title"] if failure else "see the output")
    summarizer = SUMMARIZERS.get(args[0] if args else "")
    return (summarizer(args, log) if summarizer else None) or "finished"


# -- one summarizer per command: (args, log) -> a line, or None for "finished" ----------------------


def _fetch(args: list[str], log: str) -> str:
    box = _found(r"Box: (\d+ files, \d+ MB)", log)
    return f"Fetched everything; the box has {box}" if box else "Fetched"


def _upload(args: list[str], log: str) -> str:
    tallies = re.findall(r"(\w+): (\d+) uploaded, (\d+) already there", log)
    uploaded = sum(int(t[1]) for t in tallies)
    there = sum(int(t[2]) for t in tallies)
    return f"Uploaded {uploaded} new file{_plural(uploaded)} to Gitea" + (
        f", {there} were already there" if there else ""
    )


def _gitea(args: list[str], log: str) -> str | None:
    action = _arg(args, 1)
    if action == "setup":
        version = _found(r"Gitea (\S+) is up at (\S+)", log)
        return f"Gitea {version} is installed and sealed" if version else "Gitea setup finished"
    if action == "backup":
        size = _found(r"backup done: \S+ \((\d+ MB)\)", log)
        return f"Backed up Gitea ({size})" if size else "Backed up Gitea"
    if action == "restore":
        return "Restored Gitea from a backup"
    return None


def _infra(args: list[str], log: str) -> str | None:
    if len(args) <= 2:
        return None
    action, name = args[1], args[2]
    nodes = len(re.findall(r"^  \S+: all good", log, re.M))
    what = "Set up and verified" if action == "setup" else "Verified" if action == "verify" else "Upgraded"
    return f"{what} {name}: {nodes} node{_plural(nodes)} all good" if nodes else f"{what} {name}"


def _check(args: list[str], log: str) -> str:
    passed = _found(r"(\d+/\d+) checks passed", log)
    infra = args[args.index("--infra") + 1] if "--infra" in args else "?"
    return f"Smoke checks on {infra}: {passed} passed" if passed else "Smoke checks passed"


def _demo(args: list[str], log: str) -> str | None:
    if _arg(args, 1) != "push":
        return None
    files = _found(r"committed (\d+) file", log)
    if not files:
        return "Pushed the demo; nothing had changed"
    return f"Pushed the demo ({files} file{'s' if files != '1' else ''} changed); Gitea Actions is deploying it"


def _egress(args: list[str], log: str) -> str | None:
    if len(args) <= 2:
        return None
    server, action = args[1], args[2]
    return {
        "seal": "Switched the internet off on " + server,
        "open": "Switched the internet ON for " + server,
        "status": "Checked the seal on " + server,
    }.get(action, "egress")


def _lab(args: list[str], log: str) -> str:
    return "Created the lab servers" if _arg(args, 1) == "up" else "Removed the lab servers"


def _proxy(args: list[str], log: str) -> str:
    return "Set up and verified the egress proxy" if _arg(args, 1) == "setup" else "Verified the egress proxy"


def _always(line: str):
    return lambda args, log: line


SUMMARIZERS = {
    "fetch": _fetch,
    "upload": _upload,
    "gitea": _gitea,
    "infra": _infra,
    "check": _check,
    "demo": _demo,
    "egress": _egress,
    "lab": _lab,
    "proxy": _proxy,
    "pack": _always("Packed the box into signed archives"),
    "needs": _always("Listed the needs"),
    "hosts": _always("Refreshed the machine facts"),
    "pki": _always("Rotated the CA and reissued certificates"),
    "report": _always("Wrote the proof report"),
}


def _found(pattern: str, log: str) -> str | None:
    """The first group of the first match of pattern in log, or None."""
    m = re.search(pattern, log)
    return m.group(1) if m else None


def _arg(args: list[str], index: int) -> str | None:
    return args[index] if len(args) > index else None


def _plural(count: int) -> str:
    return "s" if count != 1 else ""
