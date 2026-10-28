"""outpost check: run the smoke checks on an infrastructure."""

from outpost.checks import checks_for, run_checks
from outpost.cli.output import say
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("check", help="run smoke checks on an infrastructure")
    parser.add_argument("name", nargs="?", help="only this check")
    parser.add_argument("--infra", required=True)
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    infra = system.infra(args.infra)
    found = checks_for(system, infra, only=args.name)
    if not found:
        named = f" named {args.name}" if args.name else ""
        say(f"No checks for {infra.type} in {system.workspace.checks_dir}{named}")
        return 1
    results = run_checks(system, infra, found, log=say)
    failed = [result["name"] for result in results if not result["ok"]]
    say()
    say(
        f"{len(results) - len(failed)}/{len(results)} checks passed"
        + (f"; failed: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0
