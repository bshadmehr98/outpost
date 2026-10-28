"""outpost lab up|down: throwaway servers in Docker, to see a full run without any VPS."""

from outpost import lab
from outpost.cli.output import say
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("lab", help="start or remove throwaway servers in Docker")
    parser.add_argument("action", choices=["up", "down"])
    parser.add_argument("--small", action="store_true", help="no k3s server (saves about 1 GB of RAM)")
    parser.set_defaults(run=run)


def run(args) -> int:
    workspace = Workspace.open(args.dir)
    if args.action == "down":
        lab.down(workspace, log=say)
        return 0
    lab.up(workspace, k3s=not args.small, log=say)
    say("Next: outpost gitea setup, outpost upload, outpost infra setup dev, outpost check --infra dev")
    return 0
