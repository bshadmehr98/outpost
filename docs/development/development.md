# Development

```
git clone ... && cd outpost
uv venv && uv pip install -e '.[dev]' pip      # or python -m venv .venv && pip install -e '.[dev]'
pytest                                           # fast, no network, no SSH
OUTPOST_INTEGRATION=1 pytest tests/integration   # real Gitea in Docker, real Docker Hub, and the full flow
```

Unit tests take about ten seconds and need no network. The integration suite takes a few minutes
and needs Docker and the internet; [testing](testing.md) describes both.

## Layout

```
outpost/
  cli.py          the commands, in the order you use them
  paths.py        the platform folder layout
  config.py       servers.yml and mirrors.yml
  versions.py     pinned Gitea, Docker, Compose, k3s versions and URLs
  needs/          needs files and lockfile parsers
  fetch/          one module per kind: images, pypi, npm, apt, files
  box.py          manifest, pack, unpack
  pki.py          CA, server cert, box signing (openssl CLI)
  oci.py          pull and push container images over HTTP
  http.py         urllib wrapper: private CA, Host header, auth, downloads
  ssh.py          Host.run / write / put and the Tunnel
  gitea/          client (API + registries), setup (install over SSH), upload, connect
  infra/          clients (config files), egress, common steps, compose, swarm, k3s, verify, runner
  demo.py         the demo project: workflow generation, push through Gitea's API, status
  checks/         smoke check loader and runner
  ui/             the dashboard: server.py (JSON endpoints), projects.py (folder scan), index.html
  lab.py          throwaway servers in Docker (`outpost lab`)
  templates/      what `outpost init` copies (incl. demo/), and the lab server's Dockerfile
tests/            unit tests with a fake SSH host and a fake Gitea speaking real HTTP
tests/integration real Gitea container, Docker Hub, and the full flow on the lab
```

## Adding a lockfile format

Write `parse_<format>(path)` in `outpost/needs/parsers.py` returning `["name==version", ...]`
or `[NpmNeed(...)]`, add one line to `parse_python_file` or `parse_js_file`, add a test.

## Adding an infrastructure type

Create `outpost/infra/<type>.py` with `setup(ctx, hosts, log)` and `extra_checks(ctx)`, call
`common.prepare_node` for each host first, register it in `outpost/infra/__init__.py` and in
`config.INFRA_TYPES`, add a test with `FakeHost`.

## Principles

- Files are the state. The CLI and the UI read and write the same folder.
- No framework. A plugin is a module with one or two functions.
- The standard library plus PyYAML. Servers get shell scripts, never Python.
- Every remote step is idempotent and logged. Verification is a separate command that only reads.
