"""outpost fetch: download everything in the needs into box/ (needs internet)."""

from outpost import box, fetch
from outpost.cli.output import say
from outpost.needs import all_needs
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("fetch", help="download everything into box/ (needs internet)")
    parser.add_argument("--only", nargs="*", choices=box.KINDS, help="fetch only these kinds")
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    only = set(args.only) if args.only else None
    results = fetch.fetch_into_box(system, all_needs(system), only=only, log=say)
    manifest = results["manifest"]
    say()
    say(f"Box: {manifest['files']} files, {manifest['bytes'] / 1e6:.0f} MB in {system.workspace.box_dir}")
    problems = fetch.failures(results)
    if problems:
        say("Some things could not be fetched:")
        for problem in problems:
            say(f"  - {problem}")
        return 1
    return 0
