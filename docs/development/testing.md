# Testing

Two layers. The unit tests prove the logic with fakes and run in seconds; the integration
tests prove the whole thing against real software and run in minutes.

```
.venv/bin/python -m pytest                                   # unit: ~134 tests, ~12 s
OUTPOST_INTEGRATION=1 .venv/bin/python -m pytest tests/integration   # real Gitea, Docker Hub, the lab
```

## Unit tests and their fakes

`tests/conftest.py` provides two doubles:

- **`FakeHost`** stands in for `outpost.ssh.Host`. It records every script and file that a
  command would send to a server, and answers scripts from a list of `(regex, stdout)` pairs.
  Its `run()` has the real signature: a failing `Result` is returned, and raises `CommandFailed`
  only with `raise_on_failure=True`.
  Tests then assert on what was sent and in which order: "the binary was not re-uploaded",
  "the seal came before the Docker install", "no `generate-access-token` on the second run".
- **`FakeGitea`** is a real HTTP server in a thread that implements enough of Gitea's API and
  of the OCI registry protocol (blob upload, manifest put, 409 on duplicates) for the client,
  the upload and the demo push to be exercised end to end over actual sockets.

What the unit suite covers, by file:

| file | proves |
|---|---|
| `test_needs.py`, `test_lockfiles.py`, `test_distros.py`, `test_projects.py` | needs files merge, and what Outpost adds to them; every lockfile format parses; distro names and families; the folder scanner finds lockfiles, `FROM` lines and apt packages |
| `test_fetch.py`, `test_oci.py`, `test_box.py`, `test_http.py` | each fetch kind with a stub registry; OCI pull/push/layout; manifest, pack, unpack, signature, Gitea's certificate; HTTP retries only for reads, file bodies streamed |
| `test_gitea_client.py`, `test_gitea_setup.py` | registry uploads against FakeGitea; the install flow's files, order and idempotence; token replacement |
| `test_infra.py` | the egress script's content; node preparation order; compose, swarm, k3s setup scripts; verify parsing |
| `test_demo.py` | workflow generation from `servers.yml`; push commits only changed files; one package token; runner labels and install |
| `test_checks.py`, `test_cli.py`, `test_config.py`, `test_lab.py`, `test_status.py`, `test_ui.py` | the check runner, every CLI command's wiring, config errors, the lab's generated files, the dashboard's sync logic and endpoints |
| `test_day_two.py` | backup and restore scripts and order; a Gitea upgrade backs up first; `infra upgrade`; Docker restarts on a version change; host facts parsing and drift; error translation and history sentences; progress steps; the report's verdict; arm64 download names; the arch check on a node; HTTP retries; the new commands' parsing |

## Integration tests

`tests/integration/test_real_gitea.py` starts `gitea/gitea:<pinned version>` in Docker and a
Docker-in-Docker daemon on a private network, and checks the real registry behaviour: generic,
PyPI, npm and Debian uploads, pushing a tiny image under every kind of name Outpost generates
(Gitea's name rule is stricter than the OCI spec, so names are checked against Gitea itself), pulling `alpine` from Docker Hub and pushing it into Gitea as
`pkgs/library/alpine`, a Docker daemon with no mirror pulling it by that name (and failing on the
bare name), and a build that uses the config image.

`tests/integration/test_full_flow.py` is the tutorial as code. It runs `outpost lab up` in
`/tmp/outpost-rig`, then the real commands over SSH, in order:

```
1  fetch           (tops up a copied OUTPOST_BOX, or fetches everything)
2a the previous Gitea release is installed and a repository created (skipped when it cannot be downloaded)
2  gitea setup     an upgrade: a backup lands in backups/, the repository survives; a second run changes nothing;
                   the token cannot call the admin API; `outpost hosts` knows the machine
2b gitea backup, then gitea restore of that archive; the token still works
3  upload          twice; the second run uploads nothing
4  infra setup dev and verify
5  check --infra dev
6  egress open: pypi.org answers; egress seal: it does not; the log has the reason
7  infra setup prod: two-node Swarm, a service with two replicas on the worker
8  infra setup k8s: verify, hello-k3s check with the pod leak test
9  demo push: wait for the workflow, then every node must serve the pushed commit with sealed_at_build and sealed_now
10 reboot: lab-node-1 and lab-k3s are restarted (systemd starts from scratch); the seal is back,
   verify passes, the demo serves again sealed, and `outpost report` says "Sealed and verified"
11 the dashboard's server-side pieces (overview, host refresh) against the real rig
```

Knobs: `OUTPOST_BOX=/path/to/box` starts from an existing box (fetch then only adds what is
missing); `OUTPOST_KEEP_RIG=1` leaves the lab running at `/tmp/outpost-rig` for a look
(`ssh -F /tmp/outpost-rig/lab/ssh_config lab-node-1`); `OUTPOST_RIG_DIR` moves it;
`OUTPOST_PREVIOUS_GITEA` picks the release step 2a installs (default: the one before the pin).
The lab uses fixed container names, so it replaces any other lab on the machine while it runs.

What the suite does not cover: a real virtual machine with a real kernel and a real reboot.
Containers share the host kernel, so a distribution's own kernel modules, cloud-init, or a
different iptables backend are not exercised. Run the tutorial on two small cloud VMs before a
production deployment; the commands are the same.

## Writing a test for a server step

Use `FakeHost` and assert on `host.scripts` and `host.files`:

```python
def test_dns_is_installed_after_the_seal(platform_dir):
    host = FakeHost("node-1")
    node.prepare_node(host, ctx, log=lambda *_: None)
    seal = next(i for i, s in enumerate(host.scripts) if "outpost-egress seal" in s)
    dns = next(i for i, s in enumerate(host.scripts) if "coredns" in s)
    assert seal < dns
```

If a behaviour only shows against the real thing (a kernel table, a Gitea quirk, a runtime's
iptables rules), add it to the full-flow test: that is where the pod leak was found.
