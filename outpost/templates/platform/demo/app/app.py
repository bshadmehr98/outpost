"""The demo site. One page that says where it runs and proves it has no internet."""

import json
import os
import platform
from importlib import metadata
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, render_template

from probe import probe_all, probe_proxy

app = Flask(__name__)
HERE = Path(__file__).parent
GITEA = os.environ.get("GITEA", "")


def read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "(not present)"


def facts() -> dict:
    build = json.loads((HERE / "build-proof.json").read_text()) if (HERE / "build-proof.json").exists() else None
    with ThreadPoolExecutor(max_workers=2) as pool:   # both at once, so /healthz stays within its probe timeout
        now, proxy = pool.submit(probe_all, GITEA or None), pool.submit(probe_proxy)
        now, proxy = now.result(), proxy.result()
    return {
        "infra": os.environ.get("INFRA", "?"),
        "node": os.environ.get("NODE", ""),
        "hostname": platform.node(),
        "sha": os.environ.get("GIT_SHA", "unknown"),
        "gitea": GITEA,
        "python": platform.python_version(),
        "packages": {p: metadata.version(p) for p in ("flask", "gunicorn", "werkzeug", "jinja2")},
        "pip_conf": read("/etc/pip.conf"),
        "apt_sources": read("/etc/apt/sources.list.d/outpost.list"),
        "build": build,
        "now": now,
        "proxy": proxy,          # None without the egress proxy variables (proxy.env / the outpost-proxy ConfigMap)
    }


@app.get("/")
def index():
    return render_template("index.html", f=facts())


@app.get("/healthz")
def healthz():
    f = facts()
    return jsonify({"ok": True, "infra": f["infra"], "sha": f["sha"], "hostname": f["hostname"],
                    "sealed_at_build": bool(f["build"] and f["build"]["sealed"]), "sealed_now": f["now"]["sealed"],
                    "gitea_ok": f["now"]["gitea_ok"], "proxy_ok": f["proxy"]["ok"] if f["proxy"] else None})
