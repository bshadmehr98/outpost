"""The dashboard: a local web page over the same workspace the CLI uses. It edits files and runs
outpost commands, nothing more.

    serve(Workspace.open("my-system"), port=8484)   # http://127.0.0.1:8484/ until Ctrl-C

    server.py           Handler and its route tables, serve(); refuses requests from other sites
    overview.py         overview(): everything the pages show, as one JSON document
    files.py            the files the page may edit: editable_file(), save_file(), save_lockfile()
    servers_form.py     write_servers(): servers.yml from the form
    reach.py            reach_server(), server_status(), refresh_hosts(): ssh from the page
    runs.py             start_run(), run_status(), past_run(), ALLOWED_COMMANDS
    status/             box status, sync arrows, preflight, run history, search
    projects/           scan a project folder into a needs file
    explain.py          a failed run in words, a finished one in one line
    index.html          the page

Code outside this folder uses only the names below.
"""

from outpost.ui.overview import overview
from outpost.ui.reach import reach_server, refresh_hosts, server_status
from outpost.ui.runs import ALLOWED_COMMANDS
from outpost.ui.server import Handler, serve

__all__ = ["ALLOWED_COMMANDS", "Handler", "overview", "reach_server", "refresh_hosts", "serve", "server_status"]
