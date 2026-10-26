# My platform

This folder is read by [Outpost](https://github.com/bshadmehr98/outpost). Commit it, except `secrets/` and `box/`.

| File or folder | What it is |
|---|---|
| `servers.yml` | which SSH hosts are the Gitea server and the infrastructures |
| `needs/*.yml` | what your projects need: images, pip, npm, apt packages, files |
| `checks/` | smoke checks that prove an infrastructure works offline |
| `mirrors.yml` | where to download from, if the defaults are blocked |
| `pki/` | your CA certificate and the signing public key (public, commit) |
| `secrets/` | private keys and the Gitea token (never commit) |
| `state/` | what Outpost did last |
| `box/` | downloaded artifacts, made by `outpost fetch` |
