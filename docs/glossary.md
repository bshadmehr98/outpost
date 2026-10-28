# Glossary

One word for each idea. Code, docs, logs and the dashboard use these words, and only these.
When today's code uses another word, the table says which one wins; the old word goes away as the
code is cleaned up.

## The big picture

**air-gapped system**
: A set of servers that do their work without reaching the internet: one Gitea server, its nodes,
  and maybe an egress proxy. Outpost builds and looks after one air-gapped system per workspace.

**AirGappedSystem**
: The context object (`outpost/system.py`) that knows the whole state of one air-gapped system: its workspace, its
  config, its state and its secrets. It only holds and reads things; it has no logic of its own.
  Commands get one and pass it on, instead of passing the workspace, the config and the
  secrets one by one.

**workspace**
: The folder made by `outpost init`, and everything Outpost keeps in it. One workspace is one
  air-gapped system. In code it is class `Workspace` in `outpost/workspace.py`. Older docs call
  it "platform folder": **workspace** wins.

**config**
: What the user writes by hand to describe the system: `servers.yml` and `mirrors.yml` (and the
  needs files and checks). Outpost reads the config and does not change it, except when the
  dashboard edits a file for the user.

**state**
: What Outpost recorded about what it did last: the JSON files in `state/`, one per step
  (`state/fetch.json`, `state/infra-dev.json`, ...). Safe to commit. The dashboard and the
  report read it.

**secrets**
: Private keys, passwords and tokens, in `secrets/`, mode 600. Never committed.

**laptop**
: The machine where you run `outpost`. It has internet during `fetch`, and reaches the servers
  over ssh. "this machine" is fine in code that runs locally ("a port on this machine"). Don't say
  "local host" or "controller".

## Servers

**server**
: Any machine Outpost manages over ssh: the Gitea server, a node, or the proxy host. In code a
  server is a `Host` (see `outpost/ssh`).

**alias**
: A server's name in your ssh config, such as `gitea-1`. `servers.yml` names servers by alias.

**Gitea server**
: The one server that runs Gitea and serves everything the nodes need. Also "the Gitea host" in
  code, where it means the `Host` object; in prose, say **Gitea server**.

**infrastructure** (short: **infra**)
: A named group of nodes that run one kind of runtime: `compose`, `swarm` or `k3s`. Declared
  under `infrastructures:` in `servers.yml`, for example `dev` or `prod`. Use **infra** in code
  names and commands (`outpost infra setup dev`), **infrastructure** in prose.

**infrastructure type**
: `compose`, `swarm` or `k3s`. Not "kind" (that is for box contents) and not "runtime".

**node**
: A server in an infrastructure, that runs workloads. The Gitea server is not a node. One server
  can be a node of several infrastructures.

**workload**
: What the user runs on the nodes: containers, Swarm services, k3s pods.

**lab**
: Throwaway servers in Docker made by `outpost lab up`, so the whole flow runs on one laptop.
  The integration tests use the lab too.

## Getting things in

**needs**
: What your projects need: images, Python and npm packages, OS packages, plain files. One YAML
  file per project in `needs/`, called a **needs file**. Outpost merges them into one list.

**kind**
: One type of thing in the needs and in the box: `images`, `pypi`, `npm`, `apt`, `apk`, `rpm`,
  `files`. Each kind has one fetch module and one Gitea registry.

**fetch**
: Download everything in the needs into the box (`outpost fetch`). The only step that needs the
  internet. Not "download" or "pull" as the name of the step.

**mirror**
: Where fetch downloads from: PyPI, npm, Docker Hub, the distro archives. The defaults can be
  changed in `mirrors.yml`. Gitea is not called a mirror.

**box**
: The folder `box/` with everything fetch downloaded, and its manifest (every file with its
  sha256). It can be carried to the system on a USB stick.

**pack**
: Turn the box into signed `tar.xz` files (`outpost pack`), to carry or publish it.

**upload**
: Push the box into Gitea's registries (`outpost upload`). Not "push" or "sync" as the name of
  the step.

**registry**
: One of Gitea's package stores: generic, PyPI, npm, Debian, Alpine, RPM, and the container
  registry. Each kind goes into one registry.

**config image**
: The `config` container image that `upload` builds. Dockerfiles copy from it
  (`COPY --from=config / /`) to get pip, npm, apt, apk, dnf and CA settings that point at Gitea.

## Setting servers up

**setup**
: Install and configure, and safe to run again: `outpost gitea setup`, `outpost infra setup`,
  `outpost proxy setup`. Every setup step is idempotent.

**seal**
: Block a server's outbound internet with iptables, but keep Gitea, private networks and DNS
  reachable. A server that is sealed is **sealed**; **open** is the opposite (`outpost egress`).
  **egress** is the outbound traffic that the seal blocks; use it in names (`egress.py`,
  `outpost-egress`), and **seal** for the action.

**egress proxy**
: The optional tinyproxy that lets sealed workloads reach a short list of allowed names, such as
  `api.twilio.com`. Declared under `egress_proxy:` in `servers.yml`. Say **egress proxy**, not
  "proxy" alone, in prose and docs; `proxy` is fine inside `outpost/infra/proxy.py` and in the
  command `outpost proxy setup`.

**proxy host**
: The server the egress proxy runs on. By default it is the Gitea server.

**runner**
: A Gitea Actions runner on the first node of each infrastructure, in host mode, so a
  `git push` can build and deploy.

**pki** / **CA**
: Outpost's own certificate authority (CA) and the certificates and keys made from it: the CA,
  Gitea's certificate, and the key that signs the box. Public parts in `pki/`, private keys in
  `secrets/`. Every node trusts the CA. **pki** is the folder and the command (`outpost pki
  rotate`); **CA** is the certificate authority itself.

## Proving it works

**verify**
: Read-only checks that a node uses Gitea and nothing else, and is sealed
  (`outpost infra verify`, `outpost proxy verify`). Each line of the result is a
  **verification**. Verify never changes anything.

**checks**
: Smoke checks: small scenarios in `checks/*.yml` that build and run something on an
  infrastructure, to prove it works offline (`outpost check`). Today "check" also means one line
  of verify, and "ssh check" in the dashboard; from now on, **check** means a smoke check, and a
  line of verify is a **verification**.

**demo**
: The example project that `outpost init` copies into `demo/`. `outpost demo push` puts it in
  Gitea, and Gitea Actions builds and deploys it on the sealed nodes.

**report**
: One HTML page, made from `state/` only, that shows what was set up and proven
  (`outpost report`).

**dashboard**
: The local web page made by `outpost ui`. It reads and writes the same workspace as the CLI.
  Say **dashboard**, not "UI" in prose; the package is `outpost/ui`.

**run**
: One command started from the dashboard, with its log in `state/runs/`. Also "run a command on
  a server" (`Host.run`).

## Words not to use

| don't say | say |
|---|---|
| platform, platform folder | workspace |
| artifact (for box contents) | file, or the kind's word (image, wheel, package) |
| host (in prose, for a machine) | server |
| proxy (alone, in prose) | egress proxy |
| download (the step) | fetch |
| push, sync (the step) | upload |
| check (one line of verify) | verification |
| UI (in prose) | dashboard |
