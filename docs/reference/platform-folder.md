# The platform folder

Everything Outpost knows about one setup is in one directory, created by `outpost init`.
It is meant to be committed to git, minus the two folders that must not be.

```
my-platform/
  servers.yml            which SSH hosts play which role                      commit
  mirrors.yml            where to download from, if the defaults are blocked  commit
  needs/*.yml            what your projects need                              commit
  checks/                smoke checks and their folders                       commit
  demo/                  the demo project (delete when you have your own)     commit
  pki/                   ca.crt, gitea.crt, box.pub: public                   commit
  secrets/               ca.key, gitea.key, box.key, gitea-token,             NEVER commit
                         gitea-admin-password, gitea-package-token
  state/                 what Outpost did last, one JSON per step             commit or ignore
  state/runs/            logs of dashboard runs                               ignored
  box/                   the downloaded artifacts                             ignored (large)
  dist/                  packed boxes                                         ignored
  lab/                   ssh key and config for the lab servers               ignored
```

The `.gitignore` that `init` writes excludes `secrets/`, `box/`, `dist/`, `state/runs/` and `lab/`.

## State files

| file | written by | content |
|---|---|---|
| `state/fetch.json` | fetch | per kind: counts, failures |
| `state/gitea.json` | gitea setup | name, ip, org, user, version, host |
| `state/upload.json` | upload | stats per kind, distros, source |
| `state/infra-<name>.json` | infra setup | type, nodes with their facts (ip, distro), manager or server, runner |
| `state/verify-<name>.json` | infra setup, verify | `ok` or `problems`, every check per node |
| `state/proxy.json` | proxy setup, verify | the egress proxy: host, ip, port, url, allow list, tinyproxy version, when, and the last `verify` result |
| `state/check-<name>.json` | check | each check's steps and output |
| `state/demo.json` | demo push | commit, repository, Actions URL, per-infrastructure URLs |

The dashboard reads these, together with file modification times, to say what is stale: needs
newer than the box, box newer than the upload, upload newer than a verification.

## Secrets

All files in `secrets/` are written with mode 600. If the Gitea server is reinstalled, run
`gitea setup` again: it keeps the admin password, and replaces the tokens the new server does
not accept. Losing `secrets/ca.key` means a new CA and therefore running `gitea setup`,
`upload` and every `infra setup` again; keep a backup of `secrets/` somewhere safe.
