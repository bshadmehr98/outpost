import json
import os
import time

from outpost.needs import FileNeed, Needs, NpmNeed
from outpost.ui import status
from outpost.workspace import Workspace


def needs():
    n = Needs(
        images=["python:3.12-slim", "alpine:3.20"],
        pypi={"3.12": ["Flask==3.1.3", "requests"]},
        npm=[NpmNeed("chalk", "5.3.0")],
        apt={"bookworm": ["curl", "libpq-dev"]},
        files=[FileNeed("gitea", "28.2.0", "https://x/gitea", "gitea")],
    )
    return n


def test_box_status_finds_what_is_there(tmp_path):
    p = Workspace(tmp_path)
    box = p.box_dir
    (box / "images" / "alpine_3.20").mkdir(parents=True)
    (box / "images" / "alpine_3.20" / "index.json").write_text("{}")
    (box / "pypi").mkdir()
    (box / "pypi" / "flask-3.1.3-py3-none-any.whl").write_bytes(b"x" * 10)
    (box / "pypi" / "Requests-2.32.0.tar.gz").write_bytes(b"x")
    (box / "npm").mkdir()
    (box / "npm" / "chalk-5.3.0.tgz").write_bytes(b"x")
    (box / "apt" / "bookworm").mkdir(parents=True)
    (box / "apt" / "bookworm" / "curl_7.88_amd64.deb").write_bytes(b"x")
    s = status.box_status(p, needs())
    assert s["missing"] == {
        "images": ["python:3.12-slim"],
        "pypi": [],
        "npm": [],
        "apt": ["libpq-dev (bookworm)"],
        "apk": [],
        "rpm": [],
        "files": ["gitea 28.2.0"],
    }
    assert s["missing_total"] == 3 and s["kinds"]["pypi"]["files"] == 2 and s["bytes"] == 15


def test_box_status_apk_and_rpm_files_are_matched_by_name(tmp_path):
    p = Workspace(tmp_path)
    (p.box_dir / "apk" / "v3.20").mkdir(parents=True)
    (p.box_dir / "apk" / "v3.20" / "curl-8.9.1-r1.apk").write_bytes(b"x")
    (p.box_dir / "rpm" / "rocky9").mkdir(parents=True)
    (p.box_dir / "rpm" / "rocky9" / "curl-7.76.1-29.el9.x86_64.rpm").write_bytes(b"x")
    n = Needs(apk={"v3.20": ["curl", "jq"]}, rpm={"rocky9": ["curl"], "fedora40": ["git"]})
    s = status.box_status(p, n)
    assert s["missing"]["apk"] == ["jq (v3.20)"] and s["missing"]["rpm"] == ["git (fedora40)"]
    assert s["kinds"]["apk"]["files"] == 1 and s["kinds"]["rpm"]["files"] == 1
    assert status.search(p, n, "curl")["needs"] == [
        {"kind": "apk (v3.20)", "text": "curl"},
        {"kind": "rpm (rocky9)", "text": "curl"},
    ]


def test_sync_arrows_follow_file_times(tmp_path):
    p = Workspace(tmp_path)
    p.needs_dir.mkdir()
    (p.needs_dir / "a.yml").write_text("images: [x]\n")
    empty = {"missing_total": 0}
    s = status.sync(p, empty, ["dev"])
    assert (
        s["arrows"]["fetch"]["state"] == "none"
        and s["arrows"]["upload"]["state"] == "none"
        and "dev" in s["arrows"]["setup"]["why"]
    )
    p.box_dir.mkdir()
    (p.box_dir / "manifest.json").write_text("{}")
    p.write_state("upload", {})
    p.write_state("verify-dev", {"result": "ok"})
    s = status.sync(p, empty, ["dev"])
    assert {a["state"] for a in s["arrows"].values()} == {"ok"}
    # a newer box than the upload makes the upload arrow stale; a newer upload than the verification makes setup stale
    later = time.time() + 10
    os.utime(p.box_dir / "manifest.json", (later, later))
    assert status.sync(p, empty, ["dev"])["arrows"]["upload"]["state"] == "stale"
    os.utime(p.state_dir / "upload.json", (later + 10, later + 10))
    assert status.sync(p, empty, ["dev"])["arrows"]["setup"]["state"] == "stale"
    assert status.sync(p, {"missing_total": 2}, ["dev"])["arrows"]["fetch"]["why"].startswith("2 items")


def test_runs_and_search(tmp_path):
    p = Workspace(tmp_path)
    runs = p.state_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "20260101-120000-fetch.log").write_text("$ outpost fetch\nhello\n[outpost exit 1 after 3s]\n")
    (runs / "20260101-130000-needs.log").write_text("$ outpost needs\nstill running")
    r = status.runs(p, live={"20260101-130000-needs"})
    assert [x["id"] for x in r] == ["20260101-130000-needs", "20260101-120000-fetch"]
    assert r[1]["exit"] == 1 and r[0]["exit"] is None and r[0]["running"] and r[1]["when"] == "2026-01-01T12:00:00"
    assert status.runs(p)[0]["running"] is False  # not live any more: the dashboard was closed mid-run
    p.box_dir.mkdir()
    (p.box_dir / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {"path": "pypi/flask-3.1.3.whl", "size": 5},
                    {"path": "images/alpine_3.20/blobs/sha256/abc", "size": 1},
                    {"path": "images/alpine_3.20/index.json", "size": 1},
                ]
            }
        )
    )
    s = status.search(p, needs(), "FLASK")
    assert (
        s["needs"] == [{"kind": "pypi (py3.12)", "text": "Flask==3.1.3"}]
        and s["box"][0]["path"] == "pypi/flask-3.1.3.whl"
    )
    assert status.search(p, needs(), "alpine")["box"] == [
        {"path": "images/alpine_3.20/index.json", "size": 1}
    ]  # blobs are noise


def test_preflight_lists_tools(tmp_path):
    names = [c["name"] for c in status.preflight(Workspace(tmp_path))]
    assert names[:3] == ["ssh", "openssl", "docker"] and "pip" in names


def test_one_bad_image_ref_does_not_make_every_image_unneeded(tmp_path):
    """Bug: one needs image that did not parse (a digest) emptied the wanted set, so box_changes listed
    every image in the box as unneeded."""
    p = Workspace(tmp_path)
    (p.box_dir / "images" / "redis_7").mkdir(parents=True)
    (p.box_dir / "images" / "old_1").mkdir(parents=True)
    n = Needs(images=["redis:7", "python@sha256:" + "a" * 64])
    assert status.box_changes(p, n)["unneeded"] == ["image old_1"]
    assert status.box_status(p, n)["missing"]["images"] == ["redis:7", "python@sha256:" + "a" * 64]


def test_box_changes_does_not_call_python_dependencies_unneeded(tmp_path):
    p = Workspace(tmp_path)
    (p.box_dir / "pypi").mkdir(parents=True)
    (p.box_dir / "pypi" / "werkzeug-3.0.0-py3-none-any.whl").write_bytes(b"x")
    assert status.box_changes(p, Needs(pypi={"3.12": ["flask==3.0.0"]}))["unneeded"] == []
