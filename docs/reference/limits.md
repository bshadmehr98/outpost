# Limits

Things Outpost does not do, on purpose or not yet.

- **Nothing it was not told about.** There is no magic mirror of the internet. If a build
  needs something, it must be in a needs file. The upside is that every dependency is written down.
- **One architecture per platform folder.** `arch: amd64` (default) or `arm64` in `servers.yml`
  selects every download; a mixed fleet needs two platform folders. arm64 is wired through every
  fetcher but has not been run against ARM servers yet.
- **One Gitea, no high availability.** `outpost gitea backup` makes the archive and `restore`
  puts it back, and an upgrade backs up first; scheduling the backup is yours (cron on the laptop,
  or a CI job).
- **OS security updates** are mirrored only for the packages you list in `needs/os-updates.yml`;
  `outpost infra upgrade` installs them (apt, apk or dnf, by the node's family). A full distro
  mirror is tens of gigabytes and out of scope.
- **Three OS package families.** apt for Debian and Ubuntu, apk for Alpine (`v3.x` branches, not
  `edge`), rpm for Rocky, Alma, Fedora and CentOS Stream (`rocky9`, `alma9`, `fedora40`,
  `stream9`). SUSE, Arch and the rest have no kind yet, so `zypper` or `pacman` in a build is
  rejected by the seal with nothing to answer it.
- **Every image is referenced by its Gitea name** in Dockerfiles and manifests:
  `gitea.internal/pkgs/library--python:3.12-slim`, `gitea.internal/pkgs/org--app:1.4`. There is no
  registry mirror on the nodes and nothing rewrites names, so a bare `FROM python:3.12` fails at
  once on a sealed node. One edited line per Dockerfile is the price of knowing where every
  image comes from.
- **IPv6** outbound is rejected entirely when sealed; Gitea is reached over IPv4. The egress
  proxy's exception is IPv4 only too.
- **Named exceptions are HTTP and HTTPS only, and opt-in.** The [egress proxy](../architecture/proxy.md)
  lets programs that set `HTTPS_PROXY` reach the names in `egress_proxy.allow` over CONNECT to 443
  (or plain HTTP on 80). No SMTP, no other ports, no UDP; no transparent interception, so a program
  that ignores the variables stays sealed. The proxy host runs Debian, Ubuntu or the RHEL family,
  not Alpine. On Rocky, Alma and CentOS Stream tinyproxy is only in EPEL, which the rpm fetcher does
  not mirror (it reads BaseOS, AppStream and extras), so `fetch` fails to find it there; Fedora has it,
  and a Debian or Ubuntu proxy host works anywhere.
- **Ubuntu and Debian hosts** are what has been run. Node setup configures apk on an Alpine host
  and dnf on a RHEL-family host, and `infra upgrade` and `infra verify` follow the family, but
  neither has been run on a real Alpine or Rocky machine yet: only the generated scripts are
  tested. Alpine hosts have a further catch: Outpost's units (egress, DNS, Docker, the runner)
  are systemd units, and Alpine runs OpenRC, so an Alpine *host* is not a node yet; Alpine is
  supported as a *base image* for builds. RHEL-family hosts run systemd and should work.
- **Tested in containers, not yet on real virtual machines.** The lab and the integration suite
  run systemd in privileged Docker containers, including a container restart as the reboot test.
  A run on cloud VMs with a real kernel and a real reboot is the next step before calling it 1.0.
- **Port 53 on the node's IP** is taken by CoreDNS. A node that already runs a DNS server there
  needs it moved or the Corefile adjusted.
- **pip resolution for unpinned specs** happens on your laptop with wheels only. Packages
  without wheels fall back to an sdist and are fetched without their dependencies. Use lockfiles.
- **Gitea Actions runners** run in host mode only: jobs are shell steps on the node. Workflows
  cannot use `uses:` actions or job containers, since there is nothing to fetch them from.
- **Runners belong to the organisation** in `servers.yml` (`pkgs` by default), so repositories
  in that organisation can use them. The token Outpost keeps has no admin scope; registering
  instance-wide runners would need one.
