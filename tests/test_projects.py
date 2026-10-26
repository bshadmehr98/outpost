from pathlib import Path

from outpost.ui import projects


def test_distro_for_image():
    assert projects.distro_for_image("python:3.12-slim") == "bookworm"
    assert projects.distro_for_image("python:3.12-slim-bullseye") == "bullseye"
    assert projects.distro_for_image("ubuntu:24.04") == "noble"
    assert projects.distro_for_image("ubuntu:jammy") == "jammy"
    assert projects.distro_for_image("alpine:3.20") is None
    assert projects.distro_for_image("ghcr.io/org/debian:trixie-slim") == "trixie"


def test_read_dockerfile_skips_stages_and_args(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text("ARG BASE=python:3.12\nFROM ${BASE} AS build\nFROM --platform=linux/amd64 node:22-slim as web\n"
                  "RUN apt-get update \\\n  && apt-get install -y -q --no-install-recommends git ca-certificates=20230311 \\\n  && rm -rf /var/lib/apt/lists/*\n"
                  "FROM build\nFROM web\nFROM scratch\n")
    d = projects.read_dockerfile(df)
    assert d["images"] == ["node:22-slim"] and d["apt"] == ["git", "ca-certificates=20230311".split("=")[0]]


def test_python_version_sources(tmp_path):
    assert projects.python_version(tmp_path, ["python:3.11-slim"]) == "3.11"
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python = ">=3.10"\n')
    assert projects.python_version(tmp_path, []) == "3.10"
    (tmp_path / ".python-version").write_text("3.13.1\n")
    assert projects.python_version(tmp_path, []) == "3.13"
    assert projects.python_version(tmp_path / "missing", []) is None


def test_scan_skips_noise_and_limits_depth(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "package-lock.json").write_text("{}")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "requirements.txt").write_text("x==1\n")
    (tmp_path / "a" / "requirements-dev.txt").write_text("pytest==8.0.0\n")
    (tmp_path / "poetry.lock").write_text("[[package]]\nname = \"a\"\nversion = \"1.0\"\n")
    s = projects.scan(tmp_path)
    names = sorted(Path(lf["path"]).name for lf in s["lockfiles"])
    assert names == ["poetry.lock", "requirements-dev.txt"]
    assert s["images"] == [] and s["apt"] == {"distro": "bookworm", "packages": []}
    assert all(lf["error"] is None for lf in s["lockfiles"])


def test_describe_lockfile_errors_are_plain(tmp_path):
    bad = tmp_path / "Pipfile.lock"
    bad.write_text("not json")
    info = projects.describe_lockfile(bad)
    assert info["kind"] == "python" and info["error"]
    assert projects.describe_lockfile(tmp_path / "README.md")["error"].startswith("not a lockfile")


def test_read_dockerfile_recovers_image_behind_registry_variable(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text("ARG REG\nFROM ${REG}:3000/${ORG}/python:3.12-slim\nFROM ${BASE}\nFROM ${REG}/x/${IMG}\n")
    assert projects.read_dockerfile(df)["images"] == ["python:3.12-slim"]
