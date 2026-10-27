# The box

The box is the folder `outpost fetch` fills. It is the unit of transfer between the internet
and the sealed network: a plain directory with a manifest, which can be uploaded, copied to a
USB stick, or published as a release.

```
box/
  manifest.json              every file: path, size, sha256
  manifest.sig               ed25519 signature of the manifest, made by `outpost pack`
  images/<safe name>/        one OCI image layout per image
    oci-layout
    index.json               points at the manifest; remembers the original reference
    blobs/sha256/<digest>    manifest, config and compressed layers, verbatim
  pypi/                      wheels (and sdists when no wheel exists)
  npm/                       tarballs, named <name>-<version>.tgz
  apt/<distro>/              .deb files, the whole dependency tree
  apk/<branch>/              .apk files, the whole dependency tree (branch v3.20 for alpine:3.20)
  rpm/<distro>/              .rpm files, the whole dependency tree (rocky9, alma9, fedora40, stream9)
  files/<name>/<version>/<filename>
```

## Images without Docker

`outpost/oci.py` is a small, readable skopeo: it speaks the registry HTTP API directly.

- **Pull:** GET the manifest with the standard `Accept` headers; when it is a multi-arch index,
  pick `linux/amd64`; then GET every blob by digest and store it under `blobs/sha256/`. On a
  401 the registry says where to obtain a token; Outpost fetches one and retries.
- **Store:** the OCI image layout, which `docker`, `skopeo`, `crane` and containerd all read.
  The original reference (`python:3.12-slim`) is kept as an annotation in `index.json`.
- **Push:** HEAD each blob on the target; upload only the missing ones with the chunked upload
  API; PUT the manifest last. Gitea's container registry is a standard registry, so this is
  the same code path for any target.

Not needing Docker on the laptop matters: `fetch` can run in a CI job or on a machine where
Docker is not allowed, and images are stored by content digest, so a re-fetch costs nothing.

## Wheels for another machine

`fetch` runs `pip download` with `--only-binary=:all:`, the Python version from the needs file
and the manylinux platform tags, so the laptop's own Python and OS do not matter. pip resolves
dependencies against those constraints and downloads the whole set. A package with no wheel
falls back to an sdist (built on the node at install time), which is fetched without its
dependencies, so lockfiles remain the honest source.

## OS packages inside a container

Debian packages can only be resolved by the distro's own apt against the distro's own index.
So `fetch` starts a `debian:bookworm` (or `ubuntu:noble`) container, runs `apt-get update`,
walks the dependency tree with `apt-cache depends` recursively, and `apt-get download`s every
package in it, including the ones the container already has. The result installs on a slim
image, a full image or a host, because nothing is assumed to be present.

Alpine and the RHEL family work the same way with their own tools: an `alpine:3.20` container
writes `/etc/apk/repositories` from `mirrors.yml`, runs `apk update` and `apk fetch --recursive
-o /out`; a `rockylinux:9` (`almalinux:9`, `fedora:40`, `quay.io/centos/centos:stream9`)
container gets a repo file built from `mirrors.yml`, installs `dnf-plugins-core` and runs
`dnf download --resolve --alldeps --destdir /out`. All three use `--platform linux/<arch>` from
`arch:` in `servers.yml`, so an arm64 platform fetches aarch64 packages on an amd64 laptop.

Gitea's Debian registry re-indexes uploaded packages per suite (`bookworm`) and component
(`main`), and signs the index with its own key, which nodes fetch at setup. Its Alpine registry
does the same per branch (`v3.20`) and repository (`main`), signing `APKINDEX.tar.gz` with an RSA
key whose name apk must find under `/etc/apk/keys/`; its RPM registry keeps one repository per
group, and Outpost uses the distro as the group, so `rocky9` and `fedora40` metadata stay apart.

## Manifest, pack and signature

`manifest.json` is written at the end of fetch and checked at the start of upload: a box
whose files do not match is refused before anything is sent. `outpost pack` creates one
`tar.xz` per kind in `dist/`, so each file stays under the 2 GB limit of GitHub release
assets, and signs the manifest with an ed25519 key:

```
pki/box.pub          public key, committed; upload verifies with it
secrets/box.key      private key, never committed; the fetch workflow gets it as a secret
```

`outpost upload dist/*.tar.xz` unpacks into a temporary folder, verifies the signature when
both `manifest.sig` and `pki/box.pub` exist, verifies the manifest, and uploads. The
[distribution reference](../reference/distribution.md) shows the GitHub workflow that produces
a signed release every week.
