# servers.yml

Which SSH hosts play which role. Every `host` and `node` is an alias from `~/.ssh/config`.
The remote user must be root or have passwordless sudo.

```yaml
# ssh_config: lab/ssh_config # optional; an ssh config file to use instead of ~/.ssh/config
# arch: amd64                # optional; amd64 (default) or arm64: the CPU architecture of every server
gitea:
  host: gitea-1               # SSH alias
  name: gitea.internal        # name every node uses; written to /etc/hosts, no DNS needed
  ip: 10.0.0.5                # optional; detected over SSH
  org: pkgs                   # optional; Gitea organisation for pip, npm, apt and files
  user: outpost               # optional; admin user Outpost creates
  allow: [10.0.0.0/24]        # optional; extra addresses the sealed host may reach

infrastructures:
  dev:
    type: compose             # Docker + Compose plugin; nodes are independent
    nodes: [dev-1]
  prod:
    type: swarm               # Docker on all; first node is the manager, others join
    nodes: [prod-1, prod-2, prod-3]
    allow: [198.51.100.0/24]  # optional; extra reachable addresses for these nodes
  lab:
    type: k3s                 # first node is the k3s server, others are agents
    nodes: [lab-1, lab-2]
    args: "--disable traefik" # optional; extra flags for `k3s server` (or `docker swarm init`)

egress_proxy:                 # optional; named exceptions to the seal (docs/architecture/proxy.md)
  allow:                      # host names, or *.domain for a domain and every name under it
    - api.twilio.com
    - "*.sendgrid.net"
  host: gitea-1               # optional; SSH alias of the proxy host; default: the Gitea host
  port: 3128                  # optional
  distro: noble               # optional; the proxy host's apt codename or rpm distro, for fetch
  name: proxy.internal        # optional; the name nodes use when the proxy is not on the Gitea host
```

## Notes

- **One Gitea, many infrastructures.** All of them read from the same Gitea. Set up Gitea once.
- **One architecture.** `arch` applies to the whole folder: wheels, images, apt packages and every
  binary are fetched for it, and setup refuses a server whose `uname -m` disagrees.
- **Node-to-node traffic.** When an infrastructure has several nodes, each node's IP is added
  to every other node's allow list automatically, so Swarm and k3s can talk across public IPs.
- **Inbound ports** are not touched. Open 443 to the Gitea server from your nodes, 2377/7946/4789
  between Swarm nodes, 6443/8472/10250 between k3s nodes, in whatever firewall your provider gives you.
- **The Gitea host needs `git`** (Gitea calls it). Ubuntu server images have it; a minimal Debian
  may not. Setup tries the package manager once and otherwise tells you to install it.
- **Rerunning setup is safe.** Every step checks before it acts.
- **`egress_proxy.allow`** takes host names only: no `https://`, no port, no path, no IP addresses
  (addresses go in an infrastructure's `allow:`), and no bare `*.com`. Entries are lower-cased.
  `*.sendgrid.net` allows `sendgrid.net` and every name under it. An empty list is an error; leave
  the section out for no proxy.
- **`egress_proxy.distro`** tells `outpost needs` and `fetch` which tinyproxy to download (`noble`,
  `bookworm`, `rocky9`...). Without it, Outpost uses what `outpost hosts` cached about the proxy
  host (`state/hosts/<alias>.json`) and otherwise asks you to set it or run `outpost hosts --refresh`.
  Alpine is not supported as a proxy host.
- **The proxy host** defaults to the Gitea host, where nodes need nothing new. On another host its
  address joins every node's allow list and `name` goes into every node's `/etc/hosts`. After a
  change, run `outpost proxy setup` (and `outpost upload`, so the config image carries the new values).
- **Changing `gitea.name`** needs a new certificate; run `outpost gitea setup` again, then
  `outpost upload` (the config image embeds the name) and `outpost infra setup` for each infrastructure.
