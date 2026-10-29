"""sync(): needs -> box -> Gitea -> nodes, and which arrow between them is stale."""

from datetime import UTC, datetime
from pathlib import Path

from outpost.workspace import STATE_GITEA, STATE_UPLOAD, Workspace, verify_state


def sync(workspace: Workspace, box: dict, infra_names: list[str]) -> dict:
    """Three arrows (fetch, upload, setup), each 'ok', 'stale' or 'none' with a reason in words, and
    when each stage last changed.

    Args:
        box: what box_status() returned.
        infra_names: the infrastructures in servers.yml.
    """
    fetched_at = _mtime(workspace.box_dir / "manifest.json")
    needs_at = max([_mtime(p) or 0 for p in workspace.needs_dir.glob("*.yml")] + [0]) or None
    uploaded_at = workspace.state_mtime(STATE_UPLOAD)
    verified = {name: workspace.state_mtime(verify_state(name)) for name in infra_names}
    arrows = {
        "fetch": _fetch_arrow(fetched_at, box["missing_total"]),
        "upload": _upload_arrow(fetched_at, uploaded_at),
        "setup": _setup_arrow(infra_names, verified, uploaded_at),
    }
    return {
        "arrows": {name: {"state": state, "why": why} for name, (state, why) in arrows.items()},
        "times": {
            "needs": _iso(needs_at),
            "fetch": _iso(fetched_at),
            "gitea": _iso(workspace.state_mtime(STATE_GITEA)),
            "upload": _iso(uploaded_at),
            "verify": {name: _iso(at) for name, at in verified.items()},
        },
    }


def _fetch_arrow(fetched_at: float | None, missing_total: int) -> tuple[str, str]:
    if not fetched_at:
        return "none", "nothing fetched yet"
    if missing_total:
        return "stale", f"{missing_total} item{'s' if missing_total > 1 else ''} in the needs are not in the box"
    return "ok", "every need is in the box"


def _upload_arrow(fetched_at: float | None, uploaded_at: float | None) -> tuple[str, str]:
    if not uploaded_at:
        return "none", "nothing uploaded yet"
    if fetched_at and fetched_at > uploaded_at + 1:
        return "stale", "the box changed after the last upload"
    return "ok", "Gitea has the box"


def _setup_arrow(infra_names: list[str], verified: dict, uploaded_at: float | None) -> tuple[str, str]:
    pending = [name for name, at in verified.items() if not at]
    older_than_upload = [name for name, at in verified.items() if at and uploaded_at and uploaded_at > at + 1]
    if not infra_names:
        return "none", "no infrastructures in servers.yml"
    if pending:
        return ("none" if len(pending) == len(infra_names) else "stale"), f"not set up: {', '.join(pending)}"
    if older_than_upload:
        return "stale", f"uploaded after the last verification of {', '.join(older_than_upload)}; verify again"
    return "ok", "all nodes verified"


def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.exists() else None


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds") if timestamp else None
