# 2. Install Outpost

## What the laptop needs

| Tool | Why | Needed for |
|---|---|---|
| Python 3.11+ | Outpost is a Python package with one dependency, PyYAML | everything |
| `ssh` and `scp` | every server step goes over SSH, using your `~/.ssh/config` aliases | servers |
| `openssl` | creates the private CA, Gitea's certificate and box signatures | `gitea setup`, `pack` |
| Docker | resolves apt, apk and rpm packages inside a container of the right distro, and runs the lab | `fetch` (apt, apk, rpm), `lab` |
| `pip` | downloads wheels for the server's Python version | `fetch` (pypi) |

Servers need nothing but SSH as root, or as a user with passwordless sudo. Outpost never
installs Python or anything else on a server; it sends shell scripts and static binaries.

## Install

Outpost is not on PyPI yet. From a checkout of the repository:

```
pipx install .           # or: uv tool install .
outpost --help
```

For development, in a virtual environment:

```
uv venv && uv pip install -e '.[dev]' pip     # or python -m venv .venv && pip install -e '.[dev]'
```

`outpost --version` prints the version. `outpost <command> --help` explains each command; the
[CLI reference](../reference/cli.md) lists all of them.

## Check the laptop

The dashboard has a preflight card for this, but you can also just run the tools:

```
ssh -V && openssl version && docker --version && python3 -m pip --version
```

If Docker is not available, `fetch` still works for everything except OS packages (apt, apk, rpm), and you
will need real servers instead of the lab.

## A workspace

Outpost keeps everything about one setup in one directory, the *workspace*. Create one:

```
outpost init ~/my-workspace
cd ~/my-workspace
```

It contains `servers.yml` (which hosts play which role), `needs/` (what to fetch), `checks/`
(smoke tests), `demo/` (a sample project), `mirrors.yml`, and a `.gitignore` that excludes
`secrets/`, `box/`, `dist/`, `state/runs/` and `lab/`. Everything else is meant to be committed,
so an air-gapped system is reproducible from git plus a box. The [workspace reference](../reference/workspace.md)
describes every file.

From here on, every command is run inside this folder (or with `--dir ~/my-workspace`).

Next: [Build a lab](03-lab.md)
