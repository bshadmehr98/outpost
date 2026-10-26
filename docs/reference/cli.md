# CLI reference

Every command reads and writes the platform folder it is run in. `--dir <folder>` selects
another one. `outpost <command> --help` prints the options.

| command | what it does | needs |
|---|---|---|
| `outpost init [folder]` | create a platform folder: `servers.yml`, `needs/demo.yml`, `checks/`, `demo/`, `mirrors.yml`, `.gitignore` | nothing |
| `outpost needs` | print the merged needs, including the pinned platform files | nothing |
| `outpost fetch [--only kinds]` | download everything into `box/`; incremental; failures listed at the end | internet; Docker for apt; pip for pypi |
| `outpost pack [--out dist]` | one `tar.xz` per kind plus a signed manifest, for releases or USB | openssl |
| `outpost gitea setup` | install or upgrade Gitea on its server (a backup is taken before an upgrade), make certificates, admin, token, org; seal it | ssh, openssl |
| `outpost gitea info` | print the Gitea address, organisation, version and seal status | ssh |
| `outpost gitea backup [--out backups] [--keep-on-server]` | pause Gitea, archive its data and config, copy the archive here | ssh |
| `outpost gitea restore <archive>` | put a backup back on the Gitea server, keeping the current data aside | ssh |
| `outpost upload [source ...]` | push a box folder or `tar.xz` files into Gitea; build the config image | ssh |
| `outpost infra setup <name>` | prepare, seal, install the runtime and the runner on the nodes; verify | ssh |
| `outpost infra verify <name>` | re-run the checks only, print the table | ssh |
| `outpost infra upgrade <name>` | `apt-get upgrade` on every node, answered by Gitea; see [maintenance](maintenance.md) | ssh |
| `outpost infra list` | infrastructures in `servers.yml` and whether they are set up | nothing |
| `outpost check [name] --infra <name>` | run the smoke checks (or one) on the infrastructure's first node | ssh |
| `outpost demo push` | write the workflow, create the repo and secret, commit `demo/`; Actions deploys | ssh |
| `outpost demo status` | workflow runs and jobs, and what each node serves | ssh |
| `outpost egress <target> seal\|open\|status [--reason]` | flip or show the seal on `gitea`, an infrastructure, or an SSH alias | ssh |
| `outpost ui [--port 8484]` | the dashboard on localhost | nothing |
| `outpost lab up [--small]` / `down` | throwaway servers in Docker; `--small` skips k3s | Docker |
| `outpost hosts [--refresh]` | what every server runs: OS, seal, versions, workloads; `--refresh` asks over SSH | ssh for `--refresh` |
| `outpost pki rotate` | new CA and Gitea certificate, pushed to Gitea, the config image and every node | ssh, openssl |
| `outpost report [--out file]` | the proof report as one HTML page, from `state/` only | nothing |

## Exit codes and output

Commands exit 0 on success and 1 on any failure, printing one line per step as they go. The
dashboard runs the same commands and appends `[outpost exit N after Ss]` to each log.

## Environment variables

| variable | effect |
|---|---|
| `OUTPOST_SSH_CONFIG` | ssh config file to use instead of `~/.ssh/config` (what `ssh_config:` in `servers.yml` sets) |
| `OUTPOST_INTEGRATION=1` | lets the integration tests run |
| `OUTPOST_BOX`, `OUTPOST_RIG_DIR`, `OUTPOST_KEEP_RIG`, `OUTPOST_PREVIOUS_GITEA` | integration test knobs; see [testing](../development/testing.md) |

## The order you use them

```
init → needs → fetch → gitea setup → upload → infra setup → check → demo push
```

and on day two: edit needs, `fetch`, `upload`, and `infra setup` again when something about
the servers changed; `gitea backup` after an upload; `hosts`, `infra upgrade`, `pki rotate` and
`report` as described in [maintenance](maintenance.md). Everything is safe to re-run.
