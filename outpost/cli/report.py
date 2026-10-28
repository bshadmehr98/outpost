"""outpost report: one HTML page with the evidence, for an auditor or a boss."""

from pathlib import Path

from outpost.cli.output import say
from outpost.report import report_data, servers_or_none, verdict, write_report
from outpost.workspace import Workspace


def add_parser(commands) -> None:
    parser = commands.add_parser("report", help="write the proof report as one HTML page")
    parser.add_argument("--out", help="where to write it (default: state/report.html)")
    parser.set_defaults(run=run)


def run(args) -> int:
    workspace = Workspace.open(args.dir)  # not AirGappedSystem.load: the report shows a half-built system too
    out = write_report(workspace, Path(args.out) if args.out else None)
    proven = verdict(report_data(workspace, servers_or_none(workspace)))["ok"]
    say(f"wrote {out}")
    say("verdict: " + ("sealed and verified" if proven else "not fully proven; open the page for why"))
    return 0
