"""The proof report: one standalone HTML page that says what the air-gapped system is, what was
verified, when, and what the servers run. Built from the files in state/ only, so it needs no ssh
and shows exactly what the last commands recorded.

    path = write_report(workspace)               # state/report.html (or out=...)
    data = report_data(workspace, servers)       # servers may be None: a half-built system has a report too
    verdict(data)["ok"]                          # sealed and verified?
    render_html(data)                            # the page, as text

`outpost report` writes it; the dashboard serves the same page at /report.html.

    data.py         report_data(), servers_or_none(): what the page shows, from state/ and servers.yml
    verdict.py      verdict(): the one-line answer at the top
    html.py         render_html(): one function per section
    style.py        CSS
    write.py        write_report()
"""

from outpost.report.data import report_data, servers_or_none
from outpost.report.html import render_html
from outpost.report.verdict import verdict
from outpost.report.write import write_report

__all__ = ["render_html", "report_data", "servers_or_none", "verdict", "write_report"]
