# 8. Prove it

Verification says the configuration is right. The smoke checks say the whole chain works: a
real build, on the sealed node, with packages from Gitea, and a container that cannot reach out.

```
outpost check --infra dev
outpost check --infra k8s
```

## What a check is

A check is a YAML file in `checks/` with a list of shell steps, optionally with a folder of
the same name that is copied to the node. Steps run on the first node of the infrastructure,
as root, with `GITEA` and `ORG` set. A step passes when its command exits 0 and, if given, its
output contains the `expect` text. `outpost init` ships two:

**hello-python** (for `compose` and `swarm`) builds an image whose Dockerfile does
`COPY --from=config / /`, then `apt-get install` and `pip install`, runs it and expects
*Hello from Outpost*, then runs a container that tries `urlopen('https://pypi.org')` and
expects it to fail:

```
ok   docker build --build-arg GITEA=$GITEA --build-arg ORG=$ORG -t outpost-check-hello .
ok   docker run --rm outpost-check-hello                 → Hello from Outpost
ok   docker run --rm outpost-check-hello python -c "...urlopen('https://pypi.org')..." && echo LEAK || echo SEALED   → SEALED
```

**hello-k3s** (for `k3s`) runs a pod from `alpine:3.20`, which exists only in Gitea, reads its
log, then runs a second pod that must *fail* to reach pypi.org. That second pod exists because
of a hole we found and fixed; the [decisions page](../development/decisions.md) tells the story.

Results go to `state/check-<infra>.json`, and the dashboard shows them under *Prove it*.
Writing your own check is a few lines; see the [checks reference](../reference/checks.md).

## Open the internet on purpose

Sometimes a server needs the internet for a moment, to debug or to run an OS upgrade. The seal
is a switch, and every flip is logged on the server with who and why:

```
outpost egress dev open --reason "apt upgrade"
outpost egress dev status
outpost egress dev seal
ssh -F lab/ssh_config lab-node-1 cat /var/log/outpost-egress.log
```

The target is `gitea`, an infrastructure name, or an SSH alias. The dashboard has the same
buttons, with the reason as a required field, and shows each node's log.

## The negative test

The most convincing proof is a failure. On the node, try to install something that is not in
Gitea:

```
ssh -F lab/ssh_config lab-node-1 'docker run --rm python:3.12-slim pip install requests'
```

It fails within a second with pip naming Gitea's index as the only place it looked. Nothing
hangs, nothing retries against the internet. That error message is your to-do list: add
`requests` to a needs file, fetch, upload, and the same command succeeds.

Next: [Deploy a real project](09-deploy.md)
