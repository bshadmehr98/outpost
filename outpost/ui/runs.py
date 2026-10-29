"""Commands started from the dashboard: run as `python -m outpost ...`, logged to state/runs/<id>.log.

Only one runs at a time, and only the commands in ALLOWED_COMMANDS, so the page can never do
anything the CLI cannot.
"""

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from outpost.ui import explain
from outpost.ui.status import steps
from outpost.workspace import Workspace

ALLOWED_COMMANDS = {
    "needs",
    "fetch",
    "pack",
    "gitea",
    "upload",
    "infra",
    "check",
    "egress",
    "proxy",
    "lab",
    "demo",
    "hosts",
    "report",
    "pki",
}
RUNS: dict[str, dict] = {}  # run id -> {"proc", "log", "args", "started"}, for the runs of this dashboard
RUN_ID = re.compile(r"[0-9-]+-[a-z]+")  # 20260131-140501-fetch
LOG_SHOWN = 30000  # characters of a live run's log sent to the page
PAST_LOG_SHOWN = 60000  # characters of a finished run's log


def start_run(workspace: Workspace, args: list[str]) -> str:
    """Start `outpost --dir <workspace> <args>` in the background and return its run id.

    Raises:
        PermissionError: the command is not allowed, or another one is still running.
    """
    if not args or args[0] not in ALLOWED_COMMANDS:
        raise PermissionError(f"command not allowed: {' '.join(args)}")
    if live_runs():
        raise PermissionError("another command is still running; wait for it to finish")
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + args[0]
    workspace.runs_dir.mkdir(parents=True, exist_ok=True)
    log_file = workspace.runs_dir / f"{run_id}.log"
    with open(log_file, "w") as out:
        out.write("$ outpost " + " ".join(args) + "\n")
        proc = subprocess.Popen(
            [sys.executable, "-m", "outpost", "--dir", str(workspace.root), *args],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=workspace.root,
            env=_environment_that_finds_outpost(),
        )
    RUNS[run_id] = {"proc": proc, "log": log_file, "args": args, "started": time.time()}
    threading.Thread(target=_write_exit_line_when_done, args=(run_id,), daemon=True).start()
    return run_id


def run_status(run_id: str) -> dict:
    """A run of this dashboard: its log so far, its "==> " steps, and a translation of the failure.

    Raises:
        KeyError: no run with that id was started by this dashboard.
    """
    run = RUNS[run_id]
    code = run["proc"].poll()
    log = run["log"].read_text()
    return {
        "id": run_id,
        "args": run["args"],
        "running": code is None,
        "exit": code,
        "seconds": int(time.time() - run["started"]),
        "log": log[-LOG_SHOWN:],
        "steps": steps(log),
        "explain": explain.explain(log) if code not in (None, 0) else None,
    }


def past_run(workspace: Workspace, run_id: str) -> dict:
    """Any run with a log in state/runs/, also from an earlier dashboard: its log, steps and failure.

    Raises:
        PermissionError: run_id is not a run id (it would name a file outside state/runs/).
    """
    if not RUN_ID.fullmatch(run_id):
        raise PermissionError("not a run id")
    log = workspace.runs_dir / f"{run_id}.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    code = re.search(r"\[outpost exit (\d+)", text[-200:])
    return {
        "id": run_id,
        "log": text[-PAST_LOG_SHOWN:],
        "steps": steps(text),
        "explain": explain.explain(text) if code and code.group(1) != "0" else None,
    }


def live_runs() -> set[str]:
    """The ids of the runs still going."""
    return {run_id for run_id, run in RUNS.items() if run["proc"].poll() is None}


def _environment_that_finds_outpost() -> dict[str, str]:
    """This environment, with outpost's source folder on PYTHONPATH, so a source checkout works too."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _write_exit_line_when_done(run_id: str) -> None:
    """Wait for the run, then append `[outpost exit N after Ss]`, so the history knows how it ended."""
    run = RUNS[run_id]
    code = run["proc"].wait()
    with open(run["log"], "a") as out:
        out.write(f"\n[outpost exit {code} after {int(time.time() - run['started'])}s]\n")
