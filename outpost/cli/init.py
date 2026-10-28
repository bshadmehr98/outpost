"""outpost init: make a workspace to fill in."""

from pathlib import Path

from outpost.cli.output import say
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("init", help="create a workspace")
    parser.add_argument("folder", nargs="?", help="where to create it (default: current directory)")
    parser.set_defaults(run=run)


def run(args) -> int:
    target = Path(args.folder or args.dir).resolve()
    if Workspace(target).exists():
        say(f"{target} already has a servers.yml; nothing done.")
        return 0
    Workspace.create(target)
    say(f"Created a workspace in {target}")
    say("Next: edit servers.yml and needs/, then run `outpost needs` to see what will be fetched.")
    return 0
