"""What the dashboard's overview is made of: is the workspace in sync, what is missing, is this laptop ready.

    box_status(workspace, needs)        per kind: how much is in the box, and which needs are not there yet
    box_changes(workspace, needs)       what is new in the box since the last upload, and what no need asks for
    sync(workspace, box, infra_names)   needs -> box -> Gitea -> nodes: which arrow is stale and why
    preflight(workspace)                docker, ssh, openssl, pip and free disk on this laptop
    runs(workspace), steps(log)         past dashboard runs; the "==> " progress lines of one
    search(workspace, needs, q)         find a package or image in the needs and in the box

Everything is read from files; nothing here talks to a server.

    box_status.py     box_status(), box_changes(), spec_parts()
    sync.py           sync()
    preflight.py      preflight()
    run_history.py    runs(), steps()
    search.py         search()
"""

from outpost.ui.status.box_status import box_changes, box_status, spec_parts
from outpost.ui.status.preflight import preflight
from outpost.ui.status.run_history import runs, steps
from outpost.ui.status.search import search
from outpost.ui.status.sync import sync

__all__ = ["box_changes", "box_status", "preflight", "runs", "search", "spec_parts", "steps", "sync"]
