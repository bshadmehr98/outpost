# needs/*.yml

One file per project, or per purpose. All files are merged; duplicates are fine.

```yaml
name: my-api                   # only for humans
python: "3.12"                 # which Python the wheels are for (default 3.12)

images:
  - python:3.12-slim           # Docker Hub names, with a tag; lands as <gitea>/pkgs/library--python:3.12-slim
  - ghcr.io/org/app:1.4        # other registries work too; lands as <gitea>/pkgs/org--app:1.4

pypi:
  - file: ../../my-api/uv.lock          # poetry.lock, uv.lock, Pipfile.lock, requirements*.txt
  - httpx==0.27.0                        # or a pip spec; dependencies are resolved by pip

npm:
  - file: ../../my-web/package-lock.json   # pnpm-lock.yaml, yarn.lock (v1)
  - chalk@5.3.0                             # exact name@version; dependencies are NOT followed

apt:                             # one block or a list of blocks
  distro: bookworm               # the codename of the image or host that will install them
  packages: [libpq-dev, curl]

apk:                             # Alpine; one block or a list of blocks
  branch: v3.20                  # the branch of the image or host: alpine:3.20 and python:3.12-alpine3.20 are v3.20
  packages: [curl]

rpm:                             # the RHEL family; one block or a list of blocks
  distro: rocky9                 # rocky9, alma9, fedora40 or stream9 (CentOS Stream): family + major version
  packages: [curl]

files:                           # anything with a URL; stored in Gitea's generic registry
  - url: https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64
    name: jq
    version: "1.7.1"
    filename: jq                 # optional; default is the last part of the URL
```

## How each kind is fetched

| Kind | How | Needs on your laptop |
|---|---|---|
| images | pulled over HTTP straight from the registry into an OCI folder; uploaded as `pkgs/<owner>--<image>`, see [Dockerfiles](dockerfiles.md) | nothing |
| pypi | `pip download` asking for Linux wheels for the given Python; sdist as fallback | pip |
| npm | the exact tarball URL from the lockfile, or the registry's metadata for `name@version` | nothing |
| apt | the full dependency tree via `apt-cache depends` and `apt-get download`, inside a `debian:<distro>` or `ubuntu:<distro>` container | Docker |
| apk | the full dependency tree via `apk fetch --recursive`, inside an `alpine:<version>` container of the branch | Docker |
| rpm | the full dependency tree via `dnf download --resolve --alldeps`, inside a `rockylinux:9`, `almalinux:9`, `fedora:40` or `quay.io/centos/centos:stream9` container | Docker |
| files | a plain download | nothing |

Files land in Gitea at `https://<gitea>/api/packages/<org>/generic/<name>/<version>/<filename>`.

## Which distro, branch or kind?

The one your Dockerfile's base image runs, and the kind is the package manager that image has:

| base image | kind | value |
|---|---|---|
| `python:3.12-slim`, `node:20`, `debian:bookworm`, `postgres:16` | apt | `distro: bookworm` |
| `ubuntu:24.04` | apt | `distro: noble` |
| `alpine:3.20`, `python:3.12-alpine3.20`, `nginx:1.27-alpine` | apk | `branch: v3.20` (the Alpine release, written as Alpine writes it) |
| `rockylinux:9`, `almalinux:9` | rpm | `distro: rocky9`, `alma9` |
| `fedora:40`, `quay.io/centos/centos:stream9` | rpm | `distro: fedora40`, `stream9` |

A tag like `node:20-alpine` names no Alpine release; it is whatever was current when the image was
built. Pick the branch from `cat /etc/alpine-release` inside the image, or pin the versioned tag.
Hosts get their own distro or branch added automatically at `infra setup` if Gitea has packages
for it (`noble` on an Ubuntu node, `v3.20` on an Alpine node, `rocky9` on a Rocky node). The whole
dependency tree is downloaded, down to libc, so a listed package installs on a slim image, a full
image or a host alike. The tree for `curl` is about 40 files on Debian, 15 on Alpine, 20 on Rocky.

All three kinds resolve inside a container of the real distro because only the distro's own
package manager reproduces its resolver; the [box](../architecture/box.md#os-packages-inside-a-container)
page says more. In Gitea, apt packages go to the Debian registry (one suite per distro), apk
packages to the Alpine registry (one branch each, repository `main`), rpm packages to the RPM
registry (one group per distro, so `rocky9` and `fedora40` never mix).

## Lockfiles are the honest source

Pip specs and `name@version` entries are for quick additions. For anything real, point at a
lockfile: it already contains the exact resolved tree your project tested with.
