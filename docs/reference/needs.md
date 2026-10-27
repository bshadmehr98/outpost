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
| files | a plain download | nothing |

Files land in Gitea at `https://<gitea>/api/packages/<org>/generic/<name>/<version>/<filename>`.

## Which distro for apt?

The one your Dockerfile's base image runs. `python:3.12-slim` is Debian bookworm, `node:20` is
bookworm, `ubuntu:24.04` is noble. Hosts get their own codename added automatically if Gitea
has packages for it. The whole dependency tree is downloaded, down to libc, so a listed package
installs on a slim image, a full image or a host alike. The tree for `curl` is about 40 files.

## Lockfiles are the honest source

Pip specs and `name@version` entries are for quick additions. For anything real, point at a
lockfile: it already contains the exact resolved tree your project tested with.
