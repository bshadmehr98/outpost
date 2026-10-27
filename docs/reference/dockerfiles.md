# Dockerfiles that build offline

Two changes: name the base image by its Gitea copy, and copy the config image in before
anything installs packages.

```dockerfile
ARG GITEA
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config

FROM ${GITEA}/${ORG}/library--python:3.12-slim     # the Gitea copy of python:3.12-slim
COPY --from=config / /

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

Build with `docker build --build-arg GITEA=gitea.internal .`, or put `ARG GITEA=gitea.internal`
with a default. In Compose, stack and Kubernetes files the same rule applies to every `image:`:

```yaml
services:
  api:
    build:
      context: .
      args: {GITEA: gitea.internal}
  db:
    image: gitea.internal/pkgs/library--postgres:16     # the Gitea copy of postgres:16
```

## Where an image lives in Gitea

`outpost upload` pushes every image into the one organisation, next to the wheels and packages,
under the name `<owner>--<image>`, where the owner is the original registry owner and `library`
for official Docker Hub images:

| in `needs/` | in Gitea, and in your Dockerfile |
|---|---|
| `python:3.12-slim` | `gitea.internal/pkgs/library--python:3.12-slim` |
| `bitnami/redis:7` | `gitea.internal/pkgs/bitnami--redis:7` |
| `ghcr.io/org/app:1.4` | `gitea.internal/pkgs/org--app:1.4` |

The owner stays inside the name because `library/redis` and `bitnami/redis` are different images
and must not collide. `--` is valid in an OCI name and never appears at the edge of a component,
so the two halves are always clear.

## Why this works

- `FROM gitea.internal/pkgs/library--python:3.12-slim` is an ordinary pull from a registry Docker
  trusts: setup put the CA in `/etc/docker/certs.d/gitea.internal/` (and gave containerd the same
  CA in `registries.yaml` on k3s). There is no registry mirror and nothing rewrites names, so what
  the file says is what gets pulled.
- The config image carries `/etc/pip.conf`, `/etc/npmrc`, the apt source list, the apt signing
  key and the CA. Every tool inside the container finds Gitea on its own. See
  [how it works](../architecture/gitea.md#the-registries-and-their-urls).
- The container resolves `gitea.internal` because Docker hands it the node's own small DNS
  server. See [how it works](../architecture/nodes.md#dns-for-containers).

## Images from other registries

No special case. `ghcr.io/org/app:1.4` is listed in `needs/` under that name, fetched from
ghcr.io, and referenced as `gitea.internal/pkgs/org--app:1.4`, exactly like a Docker Hub image.

## Things that will not work, and what to do

| Pattern | Why it fails | Do this instead |
|---|---|---|
| `FROM python:3.12` or `image: postgres:16` | that is Docker Hub, which a sealed node rejects; nothing rewrites it | `FROM ${GITEA}/pkgs/library--python:3.12`, `image: gitea.internal/pkgs/library--postgres:16` |
| `curl https://github.com/.../tool` in a `RUN` | internet is rejected | list it under `files:`, then `curl https://gitea.internal/api/packages/pkgs/generic/tool/1.0/tool` |
| `pip install git+https://...` | same | build a wheel, list it, or vendor it |
| `npm install` without a lockfile | nothing tells Outpost what to fetch | commit `package-lock.json` and point `needs` at it |
| a tool that downloads at first run (browsers, models) | same | download on your laptop, list the file, copy it in |

The failure is always immediate and names the host it tried to reach. That is your to-do list.
The dashboard's project scan reads the Gitea names back into needs (`library--python:3.12-slim`
is proposed as `python:3.12-slim`), so a converted Dockerfile still produces the right needs file.
