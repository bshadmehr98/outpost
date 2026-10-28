# What it proves, and what it does not

Outpost's promise is narrow and checkable: **after setup, a server needs nothing from outside
Gitea, and cannot get anything from outside Gitea.** This page is the precise version.

## Guarantees

| claim | how it is enforced | how you check |
|---|---|---|
| A node can build and run with the internet off | every client is configured to use Gitea, and the box held everything the build needed | `outpost check`, and the demo's build-time probe |
| A node cannot reach the public internet | iptables `OUTPUT` and `FORWARD` reject, with a `security`-table drop as backstop | verify: `public internet rejected`, `routed traffic sealed too`; the demo page's live probe |
| Containers and pods are sealed too | the same chains on `FORWARD` and `DOCKER-USER`, evaluated for routed traffic | `hello-python` and `hello-k3s` checks |
| The seal survives a reboot | a oneshot systemd unit before Docker and k3s | verify: `egress seal survives reboot` |
| Every exception is written down | `allow:` in `servers.yml`; node-to-node addresses added automatically | `iptables -S OUTPOST` on the node |
| A named exception reaches only its names, and only for programs that ask | the egress proxy's allow list (tinyproxy `FilterDefaultDeny`), the seal's single owner exception for user `outpost-proxy` on TCP 443 and 80 on the proxy host only; programs without `HTTPS_PROXY` still hit the seal ([named exceptions](proxy.md)) | `outpost proxy verify`; every node's verify: `proxy refuses unknown names`; the `hello-proxy` checks; `/var/log/tinyproxy/tinyproxy.log` |
| Every time the internet was opened is logged | the egress script appends to `/var/log/outpost-egress.log` | the node page in the dashboard, or `cat` the file |
| Nodes only trust Gitea, over TLS | a private CA you own; no insecure flags anywhere | verify: `gitea reachable with trusted TLS` |
| Everything a build needs is listed | needs files and lockfiles are the only input to fetch | `outpost needs`; a build that needs more fails by name |
| A box is what it claims to be | `manifest.json` with SHA-256 per file; ed25519 signature on pack | `upload` refuses a mismatching box |
| A stolen `secrets/gitea-token` cannot take over Gitea | the token has `write:organization, write:repository, write:package, read:user, read:misc`; no admin, no user scope | `curl -u outpost:$token https://gitea/api/v1/admin/users` answers 403 |
| No secret shows in `ps` on a server | the API token and the admin password reach `curl` on stdin, and the runner's registration token is an environment variable; none is a command-line argument | `ps -ef` on the server during `gitea setup` or `infra setup` |
| Another web page cannot drive the dashboard | it listens on localhost only and refuses a request whose `Origin` is not its own, a POST without `application/json`, and a `Host` that is not this laptop (which also stops DNS rebinding) | [dashboard](../reference/dashboard.md) |
| Nothing drifted on a node | `verify` compares Docker, Compose, k3s, CoreDNS and runner versions with the pins and checks `pip.conf` and the egress script | `outpost infra verify`, `outpost hosts` |

## Not guaranteed

- **Root on a node can open the internet.** The seal is iptables; root can remove it. Outpost
  logs flips made through its script and makes the normal path easy, but it is not a sandbox
  against an administrator.
- **Who can push a workflow is root on the first node.** Actions jobs run as root on the node
  (host mode), so a workflow in the organisation can open that node's seal. See
  [limits](../reference/limits.md): a separate runner user would still need Docker or the k3s
  admin config, which are root-equivalent.
- **The Gitea server is a single point.** No HA. `outpost gitea backup` exists and an upgrade
  takes one by itself, but running it regularly and storing the archive elsewhere is yours.
- **Content is not vetted.** Outpost mirrors what the lockfiles say. If a lockfile pins a
  malicious version, the box contains it. The gain is that the list is explicit and reviewable.
- **Inbound traffic is untouched.** Open and close ports with your provider's firewall.
- **The laptop is trusted and online.** It holds every private key and it is where fetch
  happens. Protect `secrets/`; `outpost pki rotate` replaces the CA if it leaks.
- **OS security updates are mirrored only for the packages you list** in `needs/os-updates.yml`
  and installed by `outpost infra upgrade`. A distro mirror is tens of gigabytes and out of scope.
- **What an allowed name is used for.** With an egress proxy, every workload that sets `HTTPS_PROXY`
  can reach every allowed name, for any request; the proxy sees names, not callers or content
  (TLS stays end to end). An allowed name that hosts user content (a whole CDN, `*.amazonaws.com`)
  is an exfiltration path; allow the narrowest names that work. Programs that ignore the proxy
  variables, protocols other than HTTP(S), and DNS (port 53 is open to any resolver) are not
  covered by the proxy at all.
- **Private address ranges are allowed.** A node can reach anything on `10/8`, `172.16/12`
  and `192.168/16`. On a cloud provider that is usually your own VPC; check what else lives there.

## Reading the evidence

After `outpost infra setup` on real servers, keep three things: the verification table
(`state/verify-<name>.json`), the egress logs, and the demo page or your own equivalent of its
probe. Together they show configuration, history and live behaviour, which is what an auditor
asks for and what you would want to see yourself. `outpost report` puts them on one page.
