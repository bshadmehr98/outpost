"""verdict(): the one-line answer at the top of the report, and on the dashboard."""

from outpost.hosts import version_drift


def verdict(data: dict) -> dict:
    """How many servers, how many verified sealed, and anything wrong, from report_data().

    Returns:
        {"ok", "sealed", "open", "unreachable", "verified", "problems", "leaks", "drift", "servers"}.
        ok is True when no reachable server is open, every verification passed, no deployment
        reports internet access, and at least one infrastructure was verified.
    """
    hosts = data["hosts"]
    reachable = {alias: facts for alias, facts in hosts.items() if facts.get("reachable")}
    sealed = [alias for alias, facts in reachable.items() if _is_sealed(facts)]
    open_ = [alias for alias, facts in reachable.items() if not _is_sealed(facts)]
    verified = [name for name, infra in data["infras"].items() if infra["verify"].get("result") == "ok"]
    problems = [
        name for name, infra in data["infras"].items() if infra["verify"] and infra["verify"].get("result") != "ok"
    ]
    if _egress_proxy_has_problems(data.get("proxy") or {}):
        problems.append("egress proxy")
    leaks = [name for name, serving in data["serving"].items() if _reports_internet(serving)]
    return {
        "ok": not open_ and not problems and not leaks and bool(verified),
        "sealed": sealed,
        "open": open_,
        "unreachable": [alias for alias, facts in hosts.items() if not facts.get("reachable")],
        "verified": verified,
        "problems": problems,
        "leaks": leaks,
        "drift": {alias: version_drift(facts) for alias, facts in hosts.items() if version_drift(facts)},
        "servers": len(data["roles"]),
    }


def _is_sealed(facts: dict) -> bool:
    return str(facts.get("egress", "")).startswith("sealed")


def _egress_proxy_has_problems(proxy: dict) -> bool:
    """servers.yml has an egress proxy, and its last verification did not pass."""
    result = ((proxy.get("state") or {}).get("verify") or {}).get("result")
    return bool(proxy.get("config") and result and result != "ok")


def _reports_internet(serving: dict) -> bool:
    """The deployed demo says it had internet when it was built, or has it now."""
    health = serving.get("health")
    return bool(health) and not (health.get("sealed_at_build") and health.get("sealed_now"))
