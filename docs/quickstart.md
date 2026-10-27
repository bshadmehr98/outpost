# Quickstart

The whole flow on one page. The [tutorial](tutorial/01-the-problem.md) explains every step;
this is for the second time.

```
# laptop: python 3.11+, ssh, openssl, docker (for OS packages and the lab)
pipx install .                          # from a checkout; not on PyPI yet

outpost init my-platform && cd my-platform
outpost lab up                          # no servers? throwaway ones in Docker; writes servers.yml
                                        # real servers: edit servers.yml with ~/.ssh/config aliases
outpost needs                           # what will be fetched (needs/demo.yml + pinned platform files)
outpost fetch                           # internet → box/            (incremental)
outpost gitea setup                     # ssh: Gitea on 443, CA, admin, token, sealed
outpost upload                          # ssh tunnel: box → Gitea registries + config image
outpost infra setup dev                 # ssh: name, trust, clients, seal, CoreDNS, Docker, runner, verify
outpost infra setup prod                # two-node Swarm
outpost infra setup k8s                 # k3s
outpost check --infra dev               # build + run on the sealed node; a container cannot reach pypi.org
outpost check --infra k8s
outpost demo push                       # Flask site through Gitea Actions on all three; open the URLs it prints
outpost demo status
outpost ui                              # the same as a local web page
```

Day two:

```
# edit needs/<project>.yml or its lockfile
outpost fetch && outpost upload
outpost egress dev open --reason "debug" ; outpost egress dev seal
outpost infra verify dev
outpost lab down
```

Lab addresses: Gitea `https://172.31.252.10` (user `outpost`, password in `secrets/gitea-admin-password`),
demo at `http://172.31.252.21:8000` (dev), `:8080` (prod), `http://172.31.252.30:30080` (k8s),
pipeline at `https://172.31.252.10/pkgs/demo/actions`.
