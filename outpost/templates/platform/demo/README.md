# demo: a real project deployed with no internet

A small Flask site with pinned dependencies, a Dockerfile, and deploy files for Compose, Swarm
and k3s. `outpost demo push` commits it into Gitea; a Gitea Actions workflow then builds the
image on a sealed node and deploys it to every infrastructure in `servers.yml`.

```
demo/
  app/app.py               the site: shows where it runs and proves the internet is blocked
  app/probe.py             tries to reach the public internet (and, with an egress proxy, the allowed names through it)
  app/templates/index.html the page
  requirements.txt         pinned pip packages, fetched into the box by needs/demo.yml
  Dockerfile               FROM the Gitea copy of python:3.12-slim, COPY --from=config / /, then apt-get, pip
  compose.yml              Compose deploy (infrastructure type `compose`)
  stack.yml                Swarm deploy (`swarm`)
  k8s.yml                  Deployment + NodePort Service (`k3s`)
  .gitea/workflows/        written by `outpost demo push` from servers.yml
```

Edit anything, run `outpost demo push` again, and watch the workflow in Gitea.
