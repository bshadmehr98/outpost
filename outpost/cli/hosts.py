"""outpost hosts: what every server runs right now (versions, seal, workloads)."""

from outpost import hosts
from outpost.cli.output import say
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("hosts", help="what every server runs right now: versions, seal, workloads")
    parser.add_argument(
        "--refresh", action="store_true", help="ask the servers again instead of showing the cached answers"
    )
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    cached = hosts.cached(system.workspace)
    if args.refresh or not cached:
        say("Asking every server (one SSH round trip each)")
        facts = hosts.refresh(system.workspace, system.servers, log=say, make_host=system.host)
    else:
        facts = cached
        _say_cached(cached)
    for alias, data in facts.items():
        drift = hosts.drift(data)
        if drift:
            say(
                f"  {alias}: not at the pinned version: {', '.join(drift)}; run `outpost infra setup` (or `gitea setup`) to bring it up to date"
            )
    return 0


def _say_cached(cached: dict[str, dict]) -> None:
    for alias, data in cached.items():
        seen = data.get("seen", "?")
        if data.get("reachable"):
            installed = ", ".join(f"{k} {v}" for k, v in data["installed"].items() if v) or "nothing installed yet"
            say(
                f"  {alias:14} {data.get('pretty', '?'):24} {data.get('ip', '?'):15} {data.get('egress', '?'):14} {installed}   (seen {seen})"
            )
        else:
            say(f"  {alias:14} unreachable: {data.get('error', '')}   (seen {seen})")
    say("From state/hosts/. `outpost hosts --refresh` asks the servers again.")
