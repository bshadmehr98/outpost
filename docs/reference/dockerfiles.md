# Dockerfiles that build offline

One change: copy the config image in before anything installs packages.

```dockerfile
ARG GITEA
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config

FROM python:3.12-slim
COPY --from=config / /

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

Build with `docker build --build-arg GITEA=gitea.internal .`, or put `ARG GITEA=gitea.internal`
with a default. In Compose:

```yaml
services:
  api:
    build:
      context: .
      args: {GITEA: gitea.internal}
  db:
    image: postgres:16          # pulled through the Gitea mirror; no change needed
```

## Why this works

- `FROM python:3.12-slim` resolves through Docker's **registry mirror**, which setup pointed at
  Gitea. Docker asks Gitea for `library/python`, which is where upload put it.
- The config image carries `/etc/pip.conf`, `/etc/npmrc`, the apt source list, the apt signing
  key and the CA. Every tool inside the container finds Gitea on its own. See
  [how it works](../architecture/gitea.md#the-registries-and-their-urls).
- The container resolves `gitea.internal` because Docker hands it the node's own small DNS
  server. See [how it works](../architecture/nodes.md#dns-for-containers).

## Images from other registries

Mirrors only apply to Docker Hub names. For `ghcr.io/org/app:1.4`, write the Gitea name:

```dockerfile
FROM gitea.internal/org/app:1.4
```

## Things that will not work, and what to do

| Pattern | Why it fails | Do this instead |
|---|---|---|
| `curl https://github.com/.../tool` in a `RUN` | internet is rejected | list it under `files:`, then `curl https://gitea.internal/api/packages/pkgs/generic/tool/1.0/tool` |
| `pip install git+https://...` | same | build a wheel, list it, or vendor it |
| `npm install` without a lockfile | nothing tells Outpost what to fetch | commit `package-lock.json` and point `needs` at it |
| a tool that downloads at first run (browsers, models) | same | download on your laptop, list the file, copy it in |

The failure is always immediate and names the host it tried to reach. That is your to-do list.
