# CI on sealed nodes

A platform that can only *build* by hand is half a platform. Gitea ships Actions, a
GitHub-Actions-compatible CI, and Outpost installs its runner on the nodes so that a push to
Gitea builds and deploys without anything ever leaving the sealed network.

## The runner, in host mode

`outpost infra setup` installs the official `gitea-runner` on the first node of each
infrastructure:

| path | what |
|---|---|
| `/usr/local/bin/gitea-runner` | the binary, from Gitea's generic registry |
| `/etc/outpost/runner.yaml` | capacity 1, no cache server, work dir `/var/lib/outpost-runner/work` |
| `/var/lib/outpost-runner/.runner` | the registration, made with a registration token from the admin API |
| `outpost-runner.service` | `gitea-runner daemon`, with the labels on its command line |

The labels are the names of the infrastructures the node leads, plus `docker` when the node
has Docker, all of the form `<label>:host`. **Host mode** means a job is a shell script
executed on the node itself, as root, not inside a job container. The alternative, the
default `docker` mode, would need a job image mirrored into Gitea, `actions/checkout` and its
dependencies vendored, and Docker-in-Docker for builds. Host mode needs none of that, and the
job runs under the same seal as everything else on the node, so a workflow cannot be a way
around it.

A runner is registered once; if Gitea does not know its name any more (reinstalled server), setup
registers it again. Verify includes `actions runner active`.

## The demo workflow

`outpost demo push` writes `demo/.gitea/workflows/deploy.yml` from `servers.yml`. For the lab:

```yaml
name: deploy
on: {push: {branches: [main]}}
env: {GITEA: gitea.lab, ORG: pkgs, IMAGE: gitea.lab/pkgs/demo}

jobs:
  build:                                  # runs-on: docker  → lab-node-1
    steps:
      - curl the source archive from Gitea (automatic GITEA_TOKEN) and untar it
      - docker build --build-arg GITEA --build-arg ORG --build-arg GIT_SHA -t $IMAGE:<sha> .
      - docker login $GITEA -u outpost with ${{ secrets.PACKAGE_TOKEN }}; docker push

  dev:     needs: build, runs-on: dev     # lab-node-1: docker compose up -d --pull always
  prod:    needs: build, runs-on: prod    # lab-node-1: docker stack deploy  (replicas pull from Gitea)
  k8s:     needs: build, runs-on: k8s     # lab-k3s:    kubectl apply + rollout status
```

Each deploy job ends by polling `http://127.0.0.1:<port>/healthz` on its node until the
response carries the pushed commit, so a green job means the new version is serving, not just
that a command exited 0.

Two details worth knowing:

- **No `uses:`.** An action would have to be fetched from somewhere, and the only somewhere
  is Gitea. Every step is a `run:`.
- **Two tokens.** The automatic job token can read the repository but cannot log into the
  container registry, so `demo push` creates a token with the `write:package` scope once and
  stores it as the repository secret `PACKAGE_TOKEN`.

## Pushing without git

`demo push` commits through Gitea's contents API: it lists the repository tree, compares each
local file's git blob SHA with what is there, and sends only the changed files in one commit.
The laptop needs no git binary and no clone. For your own projects, normal `git push` over
HTTPS or SSH (port 2222) is the expected way; the demo avoids it only to keep the laptop's
requirements at "Python and ssh".

## Watching

```
outpost demo status                           # runs, jobs, what each node serves
https://172.31.252.10/pkgs/demo/actions       # Gitea's own view, with full logs
```

The dashboard's *Deploy* page shows the same, with the URLs to open.
