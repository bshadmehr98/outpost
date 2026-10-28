"""outpost report: one HTML page with the evidence, for an auditor or a boss."""

from pathlib import Path

from outpost import report
from outpost.cli.output import say
from outpost.config import ConfigError
from outpost.system import AirGappedSystem
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("report", help="write the proof report as one HTML page")
    parser.add_argument("--out", help="where to write it (default: state/report.html)")
    parser.set_defaults(run=run)


def run(args) -> int:
    workspace = Workspace.open(args.dir)
    try:
        servers = AirGappedSystem.load(workspace.root).servers
    except ConfigError:  # the report shows a half-built system too
        servers = None
    out = report.write(workspace, servers, Path(args.out) if args.out else None)
    verdict = report.verdict(report.build(workspace, servers))
    say(f"wrote {out}")
    say("verdict: " + ("sealed and verified" if verdict["ok"] else "not fully proven; open the page for why"))
    return 0
