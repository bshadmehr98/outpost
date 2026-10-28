"""outpost pki rotate: a new CA and Gitea certificate, pushed everywhere."""

from outpost.cli.output import say
from outpost.rotate_ca import rotate
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("pki", help="rotate the CA and the Gitea certificate")
    parser.add_argument("action", choices=["rotate"])
    parser.set_defaults(run=run)


def run(args) -> int:
    return 0 if rotate(AirGappedSystem.load(args.dir), log=say) else 1
