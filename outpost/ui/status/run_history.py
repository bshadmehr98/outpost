"""The commands run from the dashboard, read back from their logs in state/runs/."""

import re
from datetime import datetime

from outpost.ui import explain
from outpost.workspace import Workspace

EXIT_LINE = re.compile(r"\[outpost exit (\d+)")  # the trailer the dashboard appends when a run ends
SECONDS = re.compile(r"after (\d+)s")


def runs(workspace: Workspace, limit: int = 50, live: set[str] | None = None) -> list[dict]:
    """Past runs from state/runs/*.log, newest first: when, which command, how it ended, in one line.

    Args:
        live: the runs this dashboard is still executing. A log without an exit line that is not live
            was cut short (the dashboard was closed): exit None, running False.
    """
    folder = workspace.runs_dir
    if not folder.exists():
        return []
    return [
        _run(log.stem, log.read_text(errors="replace"), log.stat().st_size, live or set())
        for log in sorted(folder.glob("*.log"), reverse=True)[:limit]
    ]


def steps(log: str) -> list[dict]:
    """Every '==> ' line of a run as a step; all but the last are done, the last too once the run ended."""
    found = [line[4:].strip() for line in log.splitlines() if line.startswith("==> ")]
    ended = "[outpost exit " in log
    out = []
    for i, text in enumerate(found):
        m = re.match(r"\[(\d+)/(\d+)\]\s*(.*)", text)
        out.append(
            {
                "text": m.group(3) if m else text,
                "n": int(m.group(1)) if m else None,
                "of": int(m.group(2)) if m else None,
                "done": i < len(found) - 1 or ended,
            }
        )
    return out


def _run(run_id: str, text: str, size: int, live: set[str]) -> dict:
    """One run's history entry, from its id (`<YYYYmmdd-HHMMSS>-<command>`) and its log."""
    first_line = text.split("\n", 1)[0]
    last_line = text.rstrip().rsplit("\n", 1)[-1]
    ended = EXIT_LINE.search(last_line)
    seconds = SECONDS.search(last_line) if ended else None
    command = first_line[2:] if first_line.startswith("$ ") else first_line
    args = command.split()[1:] if command.startswith("outpost ") else command.split()
    exit_code = int(ended.group(1)) if ended else None
    running = ended is None and run_id in live
    return {
        "id": run_id,
        "when": _started_at(run_id),
        "command": command,
        "args": args,
        "exit": exit_code,
        "running": running,
        "bytes": size,
        "summary": "running" if running else explain.summarize(args, text, exit_code),
        "seconds": int(seconds.group(1)) if seconds else None,
    }


def _started_at(run_id: str) -> str:
    stamp = run_id[:15]
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return stamp
