# Day-two commands: backup, updates, rotation, evidence

Once an air-gapped system runs, five things matter over time. Each is one command, and each has a card on
the dashboard's **Keep it healthy** page that says what it will do before you press it.

## What every server runs: `outpost hosts`

```
outpost hosts             # from the cache in state/hosts/
outpost hosts --refresh   # one SSH round trip per server, then the same table
```

One line per server: OS, address, seal state, and the versions of Gitea, Docker, Compose,
buildx, k3s, CoreDNS and the Actions runner found there (and tinyproxy on the egress proxy host,
with the last 20 lines of its log). Anything not at the pinned version is
listed as drift. The facts are cached as `state/hosts/<alias>.json`, and `gitea setup`,
`infra setup` and `infra verify` refresh the hosts they touch, so the dashboard's **Machines**
page is current without asking.

## Backups: `outpost gitea backup` and `restore`

Gitea is the only copy of every package, image and repository your servers depend on.

```
outpost gitea backup                   # -> backups/gitea-<name>-<date>.tar.gz
outpost gitea backup --keep-on-server  # also leaves it in /var/tmp/outpost-backups there
outpost gitea restore backups/gitea-gitea.internal-20261026-225756.tar.gz
```

A backup stops Gitea, archives `/var/lib/gitea/data` (SQLite database, repositories, packages)
and `/etc/gitea`, starts Gitea again, copies the archive to the laptop and removes it from the
server. The pause is the time a `tar` of the data takes: about ten seconds per half gigabyte.
SQLite must not be copied while it is written to, which is why Gitea is stopped rather than
snapshotted. `state/backup.json` records the last one; the dashboard warns when there is none.

Restore copies the archive up, stops Gitea, moves the current data to
`data.before-restore-<date>`, extracts, re-installs the certificate from `pki/` (the one every
node trusts), starts Gitea and checks that `secrets/gitea-token` still works. Nothing is
deleted; remove the `before-restore` folder yourself when you are happy. Restoring onto a
fresh server means `outpost gitea setup` first (binary, user, service), then `restore`.

`backups/` is in the template `.gitignore`: the archives are large and contain your
repositories. Copy them somewhere that is not the laptop.

## Upgrading Gitea, Docker, k3s and the rest

The versions are pinned in `outpost/versions.py` and change with Outpost releases. After
updating Outpost:

```
outpost fetch --only files    # the new binaries
outpost upload                # into Gitea
outpost gitea setup           # backs Gitea up to backups/, then replaces the binary; Gitea migrates on start
outpost infra setup <name>    # replaces Docker, Compose, buildx, CoreDNS, k3s, the runner where they differ; restarts them
```

Every step only acts where the installed version differs from the pin. The Gitea upgrade is
tested in the integration suite: the previous release is installed first, a repository is
created, then the pinned release goes on top and the repository must still be there.

## Operating-system security updates: `outpost infra upgrade`

The nodes never talk to the internet, so OS updates come from Gitea like everything else.
Rename `needs/os-updates.yml.example` to `os-updates.yml`, set the distro to what the servers
run (`outpost hosts` shows it) and list the packages that matter:

```yaml
name: os-updates
apt:
  distro: noble
  packages: [openssl, libssl3t64, ca-certificates, curl, openssh-server, sudo, systemd, libc6, tzdata]
```

Then, as often as you like:

```
outpost fetch --only apt        # current versions and their dependency trees, a few hundred MB
outpost upload
outpost infra upgrade <name>    # apt-get update && apt-get upgrade on every node, from Gitea only
```

The output says how many packages changed on each node and whether a reboot is required. A
full distribution mirror would be tens of gigabytes; a list of the packages that face the
network is a few hundred megabytes and is what most audits ask for.

The upgrade is family-aware: one script reads `/etc/os-release` on the node and runs `apt-get
update && apt-get upgrade` on Debian and Ubuntu, `apk update && apk upgrade` on Alpine, and
`dnf makecache && dnf upgrade -y` on Rocky, Alma, Fedora and CentOS Stream (with
`needs-restarting -r` for the reboot hint where it exists). For RHEL-family servers the needs
file uses an `rpm:` block (`distro: rocky9`) and `fetch --only rpm`; for Alpine an `apk:` block
(`branch: v3.20`) and `fetch --only apk`. The node's own distro is tried at `infra setup`, so
packages uploaded for it are found without editing anything on the node.

## Rotating the CA: `outpost pki rotate`

Every server trusts one private CA. If `secrets/ca.key` may have leaked, or on a schedule:

```
outpost pki rotate
```

moves the old CA and Gitea certificate to `pki/archive/<date>` and `secrets/archive/<date>`,
then runs `gitea setup` (new CA, new certificate, Gitea restarted), pushes a new `config`
image, and runs `infra setup` for every infrastructure that was set up, which rewrites the CA
on every node, in the system trust store, pip, npm, apt, Docker and k3s, and restarts the
runner. Applications keep running. Images that copied the old CA into themselves (every image
built `FROM config`) keep working against the new Gitea only until they are rebuilt, so
rebuild them.

## Named exceptions: changing the allow list, reading the log

With an [egress proxy](../architecture/proxy.md), the names sealed workloads may reach are
`egress_proxy.allow` in `servers.yml` and nothing else. To change them:

```
# edit servers.yml: egress_proxy.allow
outpost proxy setup        # rewrites /usr/share/tinyproxy/outpost.allow, restarts the proxy, verifies
outpost upload             # optional: refreshes OUTPOST_PROXY_ALLOWED in the config image
```

`proxy setup` also rewrites `/etc/outpost/proxy.env`, the profile, `/etc/environment`, the Docker
client config and the k3s ConfigMap on every infrastructure already set up, so the demo's proxy
card lists the new names after its next deploy. Nothing else has to restart: the proxy reads its
list at start, and the setup restarts it. Moving the proxy to another host (`egress_proxy.host`) is
the same command; run `outpost gitea setup` afterwards if it left the Gitea host, so the Gitea
host's seal drops the exception.

What went through it:

```
ssh gitea-1 tail -f /var/log/tinyproxy/tinyproxy.log
```

```
CONNECT   Oct 28 10:00:02.311 [4211]: Request (file descriptor 7): CONNECT api.twilio.com:443 HTTP/1.1
NOTICE    Oct 28 10:00:09.802 [4211]: Proxying refused on filtered domain "example.com"
```

Every request line names the host and port asked for; every refusal names the host. URLs and
bodies of HTTPS requests are not there, because the proxy never sees them. The log is rotated
weekly and kept eight weeks (`/etc/logrotate.d/tinyproxy`). `outpost hosts --refresh` copies the
last 20 lines into `state/hosts/<alias>.json`, the Machines page and the proof report.
`outpost proxy verify` re-runs the checks.

## The evidence: `outpost report`

```
outpost report                    # -> state/report.html
outpost report --out proof.html
```

One standalone HTML page, built from `state/` alone: the Gitea server, every machine with its
seal state and versions, every verification table with its date, the smoke checks, and what
the deployed demo reports about itself, and a *Named exceptions* section: the allowed names, the
proxy host, when it was set up and verified, and the last lines of its log (or "none" without a
proxy). The dashboard serves the same page at
`/report.html`. Run `outpost infra verify`, `outpost check`, `outpost demo status` and
`outpost hosts --refresh` first if you want the evidence fresh; the page says when each piece
was recorded.

## A routine

| when | do |
|---|---|
| after every `upload` | `outpost gitea backup`, copy the archive off the laptop |
| monthly | `fetch --only apt`, `upload`, `infra upgrade` per infrastructure; `hosts --refresh`; `report` |
| on an Outpost release | `fetch --only files`, `upload`, `gitea setup`, `infra setup` per infrastructure |
| yearly, or on suspicion | `pki rotate`, rebuild images |
