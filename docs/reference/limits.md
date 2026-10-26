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
  `outpost infra upgrade` installs them. A full distro mirror is tens of gigabytes and out of scope.
- **Non-Docker-Hub images** must be referenced by their Gitea name in Dockerfiles and manifests.
- **IPv6** outbound is rejected entirely when sealed; Gitea is reached over IPv4.
- **Ubuntu and Debian hosts** are what the setup is written for. Other systemd distributions
  mostly work (the trust-store step knows RHEL too) but are untested.
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
