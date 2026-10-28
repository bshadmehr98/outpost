"""outpost ui: the dashboard, a local web page over the same workspace."""

from outpost.ui.server import serve
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("ui", help="open the local dashboard")
    parser.add_argument("--port", type=int, default=8484)
    parser.set_defaults(run=run)


def run(args) -> int:
    return serve(Workspace.open(args.dir), args.port)
