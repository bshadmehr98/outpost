# Changelog

All notable changes to Outpost. The format follows Keep a Changelog; versions follow SemVer.

## Unreleased

### Added
- Two OS package kinds next to `apt`: `apk` for Alpine (`apk: {branch: v3.20, packages: [...]}`) and `rpm` for the RHEL family (`rpm: {distro: rocky9 | alma9 | fedora40 | stream9, packages: [...]}`). Both resolve the full dependency tree inside a container of the real distro (`apk fetch --recursive`, `dnf download --resolve --alldeps`), land in `box/apk/<branch>/` and `box/rpm/<distro>/`, and are uploaded to Gitea's Alpine registry (one branch each, repository `main`) and RPM registry (one group per distro). `mirrors.yml` gained `alpine`, `rocky`, `alma`, `fedora` and `centos_stream`.
- The config image and the nodes now configure apk and dnf as well as apt: `/etc/apk/repositories` and Gitea's RSA key under `/etc/apk/keys/<owner>@<fingerprint>.rsa.pub` (the name Gitea signs with, taken from the key endpoint's `Content-Disposition`), `/etc/yum.repos.d/outpost.repo` with `gpgkey` and `sslcacert`, and `/etc/outpost/gitea-rpm.asc`; the stock `*.repo` files of Rocky, Alma, Fedora and CentOS Stream are overwritten with a comment in the config image, and `/etc/apk/repositories` is blanked when Gitea has no Alpine branch. `state/upload.json` records `apk_branches` and `rpm_distros` next to `distros`.
- `infra setup` reads `ID` and `ID_LIKE` from `/etc/os-release` and configures the matching client on each node (apt, apk or dnf), parking the stock sources the same way for all three; `infra upgrade` runs `apt-get`, `apk` or `dnf` by family; `hosts` records `id_like` and `version_id`.
- The verification checks `apt sources are gitea only` and `apt update works offline` are now `package sources are gitea only` and `package index updates offline`, one script that picks apt, apk or dnf on the node.
- A third shipped smoke check, `hello-alpine`: an Alpine image built with `COPY --from=config / /` and `apk add --no-cache curl` from Gitea, run, then a container that must fail to reach pypi.org. `needs/demo.yml` lists `apk: {branch: v3.20, packages: [curl]}` for it.
- The dashboard's project scan reads `apk add` and `dnf`/`yum`/`microdnf install` lines and proposes `apk` and `rpm` blocks, with the branch or distro read from the base image (`alpine:3.20` and `python:3.12-alpine3.20` are `v3.20`, `rockylinux:9` is `rocky9`, `fedora:40` is `fedora40`).

- Named exceptions to the seal: an optional `egress_proxy` section in `servers.yml` (`allow:` host names or `*.domain` wildcards; optional `host`, default the Gitea host, `port`, default 3128, `distro`, `name`). `outpost proxy setup` installs tinyproxy from the distro's package through Gitea (apt on Debian and Ubuntu, dnf on the RHEL family; Alpine is refused) on the proxy host, writes `/etc/outpost/proxy.conf` and the anchored allow list `/etc/outpost/proxy.allow` (`FilterDefaultDeny Yes`), runs it as `outpost-proxy.service` under the system user `outpost-proxy`, logs every request and refusal to `/var/log/outpost-proxy.log`, and seals the host with one exception: that user may open TCP 443 and 80, through an owner match in a new `OUTPOST-OUT` chain hooked from `OUTPUT` in the filter and security tables. Every other host's egress script is unchanged. `outpost proxy verify` checks the service, the package source, the allow list, the exception, an allowed name and a refused one; `state/proxy.json` records it. See docs/architecture/proxy.md.
- `outpost needs` and `fetch` add `tinyproxy` to the apt or rpm needs of the proxy host's distro (from `egress_proxy.distro` or the cached `outpost hosts` facts).
- Nodes get the proxy variables (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, both cases, and `OUTPOST_PROXY_ALLOWED`) in `/etc/outpost/proxy.env`, `/etc/profile.d/outpost.sh`, `/etc/environment` and, on Docker nodes, `proxies.default` of root's and the runner's `~/.docker/config.json` (nothing on the Docker daemon); k3s gets a ConfigMap `outpost-proxy` in `default`. A proxy on another host also joins every node's allow list and `/etc/hosts`. `infra verify` checks the proxy from each node. The config image carries `proxy.env` and the profile exports. Without a proxy, `/etc/outpost/proxy.env` is a comment so `env_file:` can always name it.
- Smoke checks can say `requires: [proxy]` and are skipped without one; new `hello-proxy` (Compose, Swarm) and `hello-proxy-k3s` checks; check steps get `PROXY_URL`, `PROXY_ALLOWED` and `PROXY_REFUSED`.
- The demo reads the proxy variables (`env_file` in compose.yml and stack.yml, an optional `envFrom` in k8s.yml), shows a "through the proxy" card, and `/healthz` reports `proxy_ok` (null without a proxy). The build-time probe still tests the direct path only.
- `outpost hosts` records the tinyproxy version, `outpost-proxy`'s state and the last 20 lines of the proxy log; the proof report has a *Named exceptions* section; the dashboard has an egress proxy card on the Servers page, the proxy role on Machines, and Set up / Verify buttons on Keep it healthy.

### Changed
- The verification check `public internet rejected` runs `curl --noproxy '*'`, so it tests the direct path even where a session carries proxy variables.
- Container images now live under the one Gitea organisation like every other kind of package, as `<owner>--<image>`: `python:3.12-slim` is uploaded to `<gitea>/pkgs/library--python:3.12-slim`, `bitnami/redis:7` to `<gitea>/pkgs/bitnami--redis:7`, `ghcr.io/org/app:1.4` to `<gitea>/pkgs/org--app:1.4`. No organisation per image owner is created any more.
- Dockerfiles, Compose, stack and Kubernetes files reference the Gitea copy explicitly (`FROM ${GITEA}/${ORG}/library--python:3.12-slim`). Nothing on a node rewrites image names: `daemon.json` no longer sets `registry-mirrors` and k3s's `registries.yaml` no longer mirrors `docker.io`. A bare Docker Hub name on a sealed node fails at once, like any other reach for the internet. The verification check `docker uses gitea mirror` is replaced by `docker trusts and reaches gitea registry`.
- The demo and the `hello-python` and `hello-k3s` checks use the explicit names; the dashboard's project scan reads a Gitea name back into the need that produces it (`library--python:3.12-slim` is proposed as `python:3.12-slim`).
- Migration: re-run `outpost upload` so images land under `pkgs`, re-run `outpost infra setup <name>` so `daemon.json` and `registries.yaml` are rewritten, and change the `FROM` and `image:` lines of your projects to the Gitea names (`outpost init` templates and `demo/` are updated; an existing `demo/` folder is yours to edit).

## 0.1.0 - 2026-10-26

First public release.

### What it does
- `fetch` downloads what lockfiles, Dockerfiles and needs files ask for (PyPI wheels, npm tarballs, apt packages with their dependency trees, container images, plain files) into one folder, the box, plus the pinned Gitea, Docker, Compose, buildx, CoreDNS, k3s and Actions runner binaries.
- `gitea setup` installs a single-binary Gitea over SSH with a private CA, an admin, a narrowly scoped token and an organisation, then seals the server.
- `upload` pushes the box into Gitea's PyPI, npm, Debian, generic and container registries and builds the `config` image.
- `infra setup` prepares Compose, Swarm or k3s nodes: trust, client configuration, seal, then CoreDNS, Docker or k3s and the Actions runner installed from Gitea; `infra verify` proves it with a table of checks.
- `check` runs smoke scenarios; `demo push` deploys a real application through Gitea Actions on the sealed nodes, with a build-time and a live internet probe.
- The seal is an iptables chain built in the filter table (REJECT) and the security table (DROP), hooked into OUTPUT, FORWARD and DOCKER-USER, restored at boot, so hosts, containers and pods are all covered.
- `lab up` gives four throwaway servers in Docker to run the whole thing without a VPS.
- Day two: `hosts` (what every server runs), `gitea backup` and `gitea restore`, automatic backup before a Gitea upgrade, `infra upgrade` (OS security updates from Gitea), `pki rotate`, `report` (one HTML page of evidence).
- `outpost ui`: a local dashboard for people who do not want to type commands: a Home page with three questions, a Machines page, Applications, step-by-step pages that say what each button will do, narrated progress, failures translated into a sentence and a fix, a history in plain words, and the proof report.
- Architectures: `arch: amd64` (default) or `arm64` in `servers.yml` selects every download.

### Known limits
See docs/reference/limits.md. Tested on Ubuntu 24.04 containers in the lab; no run on real virtual machines yet.
