# Checks

Smoke checks are small scenarios run on the first node of an infrastructure, as root, with
the environment variables `GITEA` and `ORG` set. They prove the whole chain works offline.

```
checks/hello-python.yml
checks/hello-python/            optional folder with the same name, copied to the node
  Dockerfile
  app.py
```

```yaml
name: hello-python
description: Build with apt + pip from Gitea, run it, confirm the internet is blocked.
infra: [compose, swarm]         # which infrastructure types; default all
steps:
  - run: docker build --build-arg GITEA=$GITEA -t check-hello .
  - run: docker run --rm check-hello
    expect: Hello from Outpost   # optional substring that must appear in the output
```

A step passes when its command exits 0 and, if given, `expect` appears in the output.
Steps stop at the first failure and the last lines of output are printed.

```
outpost check --infra dev              # all checks that apply
outpost check hello-python --infra dev # one check
```

Results are kept in `state/check-<infra>.json` and shown in `outpost ui`.

`outpost init` ships three: `hello-python` for Docker-based infrastructures, `hello-alpine`
(also Docker-based), which builds `$GITEA/$ORG/library--alpine:3.20` with `COPY --from=config / /`
and `apk add --no-cache curl` answered by Gitea's Alpine registry, runs `curl --version` and
confirms a container cannot reach pypi.org, and `hello-k3s`, which runs a pod from
`$GITEA/$ORG/library--alpine:3.20`, the Gitea copy of `alpine:3.20`, then a second pod that must fail to reach pypi.org.
The demo needs file lists `apk: {branch: v3.20, packages: [curl]}` for `hello-alpine`.

## The verification table

`outpost infra verify` is different from a smoke check: it is a fixed list of one-line commands
run on every node, each of which must exit 0. Besides the configuration and seal checks (Gitea in
`/etc/hosts` and the unit that restores it at boot, trusted TLS, pip and npm pointing at Gitea, `package sources are gitea only` and
`package index updates offline` for whichever of apt, apk or dnf the node has, `egress sealed`, `public internet
rejected`, `routed traffic sealed too`, `egress seal survives reboot`) it checks for drift:
Docker, Compose, k3s, CoreDNS and the runner at the pinned versions, the egress script being the
current one, and `/etc/pip.conf` unchanged. A verified node is therefore also an unchanged node.
