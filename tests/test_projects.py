from pathlib import Path

from outpost.ui import projects


def test_distro_for_image():
    assert projects.distro_for_image("python:3.12-slim") == "bookworm"
    assert projects.distro_for_image("python:3.12-slim-bullseye") == "bullseye"
    assert projects.distro_for_image("ubuntu:24.04") == "noble"
    assert projects.distro_for_image("ubuntu:jammy") == "jammy"
    assert projects.distro_for_image("ghcr.io/org/debian:trixie-slim") == "trixie"
    # apk branches: a versioned alpine tag names its branch, a bare `-alpine` tag gets the branch the demo pins
    assert projects.distro_for_image("alpine:3.20") == "v3.20"
    assert projects.distro_for_image("python:3.12-alpine3.20") == "v3.20"
    assert projects.distro_for_image("node:20-alpine") == projects.ALPINE_CURRENT == "v3.20"
    assert projects.distro_for_image("alpine:latest") == "v3.20"
    # rpm distros
    assert projects.distro_for_image("rockylinux:9") == "rocky9"
    assert projects.distro_for_image("rockylinux:9.3-minimal") == "rocky9"
    assert projects.distro_for_image("almalinux:9") == "alma9"
    assert projects.distro_for_image("fedora:40") == "fedora40"
    assert projects.distro_for_image("quay.io/centos/centos:stream9") == "stream9"
    assert projects.distro_for_image("fedora:latest") is None
    assert [projects.kind_for_distro(d) for d in ("bookworm", "v3.20", "rocky9", None)] == ["apt", "apk", "rpm", None]


def test_read_dockerfile_skips_stages_and_args(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text(
        "ARG BASE=python:3.12\nFROM ${BASE} AS build\nFROM --platform=linux/amd64 node:22-slim as web\n"
        "RUN apt-get update \\\n  && apt-get install -y -q --no-install-recommends git ca-certificates=20230311 \\\n  && rm -rf /var/lib/apt/lists/*\n"
        "FROM build\nFROM web\nFROM scratch\n"
    )
    d = projects.read_dockerfile(df)
    assert d["images"] == ["node:22-slim"] and d["apt"] == ["git", "ca-certificates"]
    assert d["apk"] == [] and d["rpm"] == []


def test_read_dockerfile_collects_apk_and_rpm_packages_separately(tmp_path):
    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM alpine:3.20\nRUN apk add --no-cache curl \\\n    jq=1.7.1-r0 && rm -rf /var/cache/apk/*\n"
        "FROM rockylinux:9\nRUN dnf install -y --setopt=install_weak_deps=False git python3-pip && dnf clean all\n"
        "RUN microdnf install -y tar; yum install -y gzip\n"
        "RUN apt-get install -y curl\n"
    )
    d = projects.read_dockerfile(df)
    assert d["images"] == ["alpine:3.20", "rockylinux:9"]
    assert d["apk"] == ["curl", "jq"] and d["rpm"] == ["git", "python3-pip", "tar", "gzip"] and d["apt"] == ["curl"]


def test_scan_and_write_needs_emit_apk_and_rpm(tmp_path, platform_dir):
    from outpost.needs import load_needs
    from outpost.paths import Platform

    project = tmp_path / "mixed"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM python:3.12-alpine3.20\nRUN apk add --no-cache libpq\n")
    (project / "Dockerfile.el").write_text("FROM almalinux:9\nRUN dnf install -y postgresql-libs\n")
    s = projects.scan(project)
    assert s["apt"] == {"distro": "bookworm", "packages": []}
    assert s["apk"] == {"branch": "v3.20", "packages": ["libpq"]} and s["rpm"] == {
        "distro": "alma9",
        "packages": ["postgresql-libs"],
    }
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "Dockerfile").write_text("FROM python:3.12-slim\n")
    assert "apk" not in projects.scan(plain) and "rpm" not in projects.scan(
        plain
    )  # no Alpine or RHEL image: no block proposed
    path = projects.write_needs(Platform(platform_dir), {**s, "name": "mixed"})
    assert path.name == "mixed.yml" and "apt:" not in path.read_text()  # no apt packages, so no apt block
    alone = tmp_path / "only-mixed"
    alone.mkdir()
    (alone / "mixed.yml").write_text(path.read_text())
    needs = load_needs(alone)
    assert needs.apk == {"v3.20": ["libpq"]} and needs.rpm == {"alma9": ["postgresql-libs"]}


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
    (tmp_path / "poetry.lock").write_text('[[package]]\nname = "a"\nversion = "1.0"\n')
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


def test_read_dockerfile_maps_gitea_names_back_to_needs(tmp_path):
    """A converted Dockerfile names the Gitea copy; a scan must propose the need that produces it."""
    df = tmp_path / "Dockerfile"
    df.write_text(
        "ARG GITEA=gitea.internal\nARG ORG=pkgs\nFROM ${GITEA}/${ORG}/config:latest AS config\n"
        "FROM ${GITEA}/${ORG}/library/python:3.12-slim\nCOPY --from=config / /\n"
        "FROM gitea.internal/pkgs/bitnami/redis:7\nFROM gitea.internal/pkgs/org/team/app:1.4\n"
        "FROM gitea.internal/pkgs/library/python:3.12-slim\n"
    )
    gitea = "gitea.internal/pkgs"
    assert projects.read_dockerfile(df, gitea)["images"] == ["python:3.12-slim", "bitnami/redis:7", "org/team/app:1.4"]
    assert projects.needed_image("${GITEA}/${ORG}/library/alpine:3.20") == "alpine:3.20"
    assert projects.needed_image("${GITEA}/${ORG}/bitnami/redis:7") == "bitnami/redis:7"
    assert projects.needed_image("${REG}/x/${IMG}") is None
    assert projects.needed_image("postgres:16", gitea) == "postgres:16"
    assert projects.needed_image("ghcr.io/org/app:1", gitea) == "ghcr.io/org/app:1"  # another registry stays as it is
    assert projects.needed_image("${BASE}") is None
    assert projects.scan(tmp_path, gitea)["images"] == ["python:3.12-slim", "bitnami/redis:7", "org/team/app:1.4"]
    # without the platform's Gitea name a literal Gitea name cannot be told from another registry
    assert projects.needed_image("gitea.internal/pkgs/bitnami/redis:7") == "gitea.internal/pkgs/bitnami/redis:7"
