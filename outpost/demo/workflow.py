"""The demo's Gitea Actions workflow: one `build` job, and one deploy job per infrastructure.

Jobs run on the Outpost runners, which are the nodes themselves, so nothing in the pipeline can
reach anything but Gitea.
"""

from outpost.demo.project import NAME

PORTS = {"compose": 8000, "swarm": 8080, "k3s": 30080}  # one port per type, so one node can host several

# `run:` steps only, no `uses:`: an action would have to be fetched from somewhere, and the only
# somewhere is Gitea. The automatic token GITEA_TOKEN can read the repository; pushing the image
# into Gitea's registry needs a token with the write:package scope, which `demo push` creates once
# (secrets/gitea-package-token) and stores on the repository as the Actions secret PACKAGE_TOKEN.
FETCH_SOURCE = """\
      - name: Get the source from Gitea
        run: |
          curl -fsSL -H "Authorization: token ${{ secrets.GITEA_TOKEN }}" \\
            "${{ gitea.server_url }}/api/v1/repos/${{ gitea.repository }}/archive/${{ gitea.sha }}.tar.gz" | tar -xz --strip-components=1
"""

DEPLOY_STEPS = {
    "compose": """\
      - name: docker compose up (the node pulls the image from Gitea)
        run: IMAGE=$IMAGE:${{ gitea.sha }} INFRA={name} PORT={port} NODE=$(hostname) docker compose -p demo-{name} -f compose.yml up -d --pull always --remove-orphans
""",
    "swarm": """\
      - name: docker stack deploy (every node pulls the image from Gitea)
        run: IMAGE=$IMAGE:${{ gitea.sha }} INFRA={name} PORT={port} docker stack deploy --prune -c stack.yml demo-{name}
""",
    "k3s": """\
      - name: kubectl apply (containerd pulls the image from Gitea)
        run: |
          sed -e "s|__IMAGE__|$IMAGE:${{ gitea.sha }}|" -e "s|__INFRA__|{name}|" -e "s|__PORT__|{port}|" k8s.yml | k3s kubectl apply -f -
          k3s kubectl rollout status deployment/demo --timeout=180s
""",
}

WAIT = """\
      - name: It answers on port {port} with this commit
        run: |
          for i in $(seq 1 60); do
            if curl -fsS http://127.0.0.1:{port}/healthz | tee /tmp/healthz | grep -q '"sha": *"${{ gitea.sha }}"'; then cat /tmp/healthz; exit 0; fi
            sleep 2
          done; echo "not serving commit ${{ gitea.sha }} yet"; cat /tmp/healthz 2>/dev/null; exit 1
"""


def workflow(gitea_name: str, org: str, infras: dict[str, str], user: str = "outpost") -> str:
    """The deploy workflow (.gitea/workflows/deploy.yml) for these infrastructures.

    Args:
        gitea_name: Gitea's name, as the nodes reach it.
        org: the organisation the image is pushed to.
        infras: infrastructure name -> type, one deploy job each.
        user: the Gitea user that owns PACKAGE_TOKEN.
    """
    image = f"{gitea_name}/{org}/{NAME}"
    out = [
        "# Written by `outpost demo push` from servers.yml. Edit servers.yml and push again to regenerate.",
        "# Every job runs on an Outpost runner, which is a sealed node: Gitea is the only server it can reach.",
        f"name: deploy\non:\n  push:\n    branches: [main]\nenv:\n  GITEA: {gitea_name}\n  ORG: {org}\n  IMAGE: {image}\n\njobs:",
        "  build:\n    name: build the image on a sealed node\n    runs-on: docker\n    steps:",
        FETCH_SOURCE.rstrip("\n"),
        "      - name: Build (apt and pip are answered by Gitea; the Dockerfile fails if the internet is reachable)",
        "        run: docker build --build-arg GITEA=$GITEA --build-arg ORG=$ORG --build-arg GIT_SHA=${{ gitea.sha }} -t $IMAGE:${{ gitea.sha }} .",
        f'      - name: Push the image into Gitea\'s registry\n        run: |\n          echo "${{{{ secrets.PACKAGE_TOKEN }}}}" | docker login $GITEA -u {user} --password-stdin\n          docker push $IMAGE:${{{{ gitea.sha }}}}',
    ]
    for name, itype in infras.items():
        port = PORTS[itype]
        out += [
            f"\n  {name}:\n    name: deploy to {name} ({itype})\n    needs: build\n    runs-on: {name}\n    steps:",
            FETCH_SOURCE.rstrip("\n"),
            _fill(DEPLOY_STEPS[itype], name, port),
            _fill(WAIT, name, port),
        ]
    return "\n".join(out) + "\n"


def _fill(template: str, name: str, port: int) -> str:
    """A step template with the infrastructure's name and port in it."""
    return template.replace("{name}", name).replace("{port}", str(port)).rstrip("\n")
