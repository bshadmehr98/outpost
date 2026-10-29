"""write_report(): build the report from the workspace and write it to a file."""

from pathlib import Path

from outpost.report.data import report_data, servers_or_none
from outpost.report.html import render_html
from outpost.workspace import Workspace


def write_report(workspace: Workspace, out: Path | None = None) -> Path:
    """Write the report to out (default state/report.html) and return its path.

    It reads servers.yml itself, and works while it is missing or broken.
    """
    out = out or workspace.report_file
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report_data(workspace, servers_or_none(workspace))))
    return out
