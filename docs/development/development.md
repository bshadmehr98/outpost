# Development

```
git clone ... && cd outpost
uv venv && uv pip install -e '.[dev]' pip      # or python -m venv .venv && pip install -e '.[dev]'
pytest                                           # fast, no network, no SSH
OUTPOST_INTEGRATION=1 pytest tests/integration   # real Gitea in Docker, real Docker Hub, and the full flow
```

Unit tests take about ten seconds and need no network. The integration suite takes a few minutes
and needs Docker and the internet; [testing](testing.md) describes both.

Before a commit, run `scripts/format.sh`, `ruff check outpost tests` and `pyright`; CI runs them
too. How the code is written: [style](style.md). The words it uses: [glossary](../glossary.md).

## Layout

```
outpost/
  cli/            the commands: main.py (COMMANDS, the parser), one small file per command
  workspace.py    the workspace layout: Workspace, state, secrets
  config/         servers.yml and mirrors.yml
  system.py       AirGappedSystem: workspace, config, state, secrets and Hosts, in one object
  endpoints.py    how nodes reach Gitea and the egress proxy
  rotate_ca.py    `outpost pki rotate`
  versions.py     pinned Gitea, Docker, Compose, k3s versions and URLs, and the CPU type
  distros.py      distro names and families: bookworm, v3.20, rocky9; os_family from /etc/os-release
  needs/          needs files, lockfile readers, and what Outpost adds (pinned files, tinyproxy)
  fetch/          one module per kind: images, pypi, npm, apt, apk, rpm, files
  box.py          manifest, pack, unpack, signature check
  pki.py          CA, Gitea's certificate, box signing (openssl CLI)
  oci/            pull and push container images over HTTP: refs, registry, layout
  http.py         urllib wrapper: private CA, Host header, auth, downloads, retries for reads
  ssh/            Host.run / write / put / get and the Tunnel (one module per job; see its __init__.py)
  egress.py       the seal: the outpost-egress script and unit; install_seal, seal_host, open_host, seal_status
  hostsfile.py    keep the Gitea name (and the egress proxy's) in /etc/hosts across reboots
  client_config/  the files that point pip, npm, apt, apk, dnf and Docker at Gitea, on nodes and in the config image
  gitea/          setup/ (install over ssh, in seven named steps), upload (one uploader per kind), backup,
                  connect (the tunnel), client (API), packages (the registries), service
  infra/          setup and verify (set_up_infra, verify_infra), upgrade, context (NodeContext),
                  node/ (prepare_node: the steps every node goes through), compose, swarm, k3s, runner,
                  verify (the verifications and their script), proxy/ (the egress proxy: tinyproxy)
  demo/           the demo project: workflow generation, push through Gitea's API, status
  checks/         smoke checks: check, load, run
  ui/             the dashboard: server.py (JSON endpoints), projects.py (folder scan), index.html
  lab.py          throwaway servers in Docker (`outpost lab`)
  templates/      what `outpost init` copies (incl. demo/), and the lab server's Dockerfile
tests/            unit tests with a fake SSH host and a fake Gitea speaking real HTTP
tests/integration real Gitea container, Docker Hub, and the full flow on the lab
```

## Adding a lockfile format

Write `parse_<format>(path)` in `outpost/needs/python_lockfiles.py` returning `["name==version", ...]`,
or in `outpost/needs/js_lockfiles.py` returning `[NpmNeed(...)]`, add one line to
`read_python_lockfile` or `read_js_lockfile`, add a test.

## Adding an infrastructure type

Create `outpost/infra/<type>.py` with `setup(ctx, hosts, log)` and `verifications(ctx)`, call
`node.prepare_node` for each host first, register it in `TYPES` in `outpost/infra/setup.py` and in
`config.INFRA_TYPES`, add a test with `FakeHost`.

## Principles

- Files are the state. The CLI and the UI read and write the same folder.
- No framework. A plugin is a module with one or two functions.
- The standard library plus PyYAML. Servers get shell scripts, never Python.
- Every remote step is idempotent and logged. Verification is a separate command that only reads.
