# Changelog

All notable changes to Outpost. The format follows Keep a Changelog; versions follow SemVer.

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
