"""search(): find a package or image in the needs and in the box."""

import json

from outpost.needs import Needs
from outpost.workspace import Workspace


def search(workspace: Workspace, needs: Needs, q: str, limit: int = 40) -> dict:
    """Case-insensitive substring search over the needs and the box's file names (image blobs left out).

    Returns:
        {"needs": [{"kind", "text"}], "box": [{"path", "size"}], "more": n}; only the first two keys
        when q is shorter than two characters.
    """
    q = q.strip().lower()
    if len(q) < 2:
        return {"needs": [], "box": []}
    box_hits = _box_hits(workspace, q)
    return {"needs": _needs_hits(needs, q)[:limit], "box": box_hits[:limit], "more": max(0, len(box_hits) - limit)}


def _needs_hits(needs: Needs, q: str) -> list[dict]:
    hits = [{"kind": "image", "text": ref} for ref in needs.images if q in ref.lower()]
    for python, specs in needs.pypi.items():
        hits += [{"kind": f"pypi (py{python})", "text": s} for s in specs if q in s.lower()]
    hits += [{"kind": "npm", "text": f"{n.name}@{n.version}"} for n in needs.npm if q in n.name.lower()]
    for kind, wanted in (("apt", needs.apt), ("apk", needs.apk), ("rpm", needs.rpm)):
        for distro, packages in wanted.items():
            hits += [{"kind": f"{kind} ({distro})", "text": p} for p in packages if q in p.lower()]
    hits += [{"kind": "file", "text": f"{f.name} {f.version}"} for f in needs.files if q in f.name.lower()]
    return hits


def _box_hits(workspace: Workspace, q: str) -> list[dict]:
    manifest = workspace.box_dir / "manifest.json"
    if not manifest.exists():
        return []
    return [
        {"path": item["path"], "size": item["size"]}
        for item in json.loads(manifest.read_text()).get("items", [])
        if not (item["path"].startswith("images/") and "/blobs/" in item["path"]) and q in item["path"].lower()
    ]
