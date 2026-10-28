"""outpost upload: push the box into Gitea's registries."""

from pathlib import Path

from outpost.cli.output import say
from outpost.gitea.upload import upload
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("upload", help="push the box into Gitea")
    parser.add_argument("source", nargs="*", help="box folder or tar.xz files (default: box/)")
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    upload(system, sources=[Path(source) for source in args.source], log=say)
    say("Next: outpost infra setup <name>")
    return 0
