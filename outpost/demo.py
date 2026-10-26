"""The demo: a real project, pushed to Gitea, built and deployed by Gitea Actions on sealed nodes.

  outpost demo push      copy demo/ into the platform if missing, write its workflow from servers.yml,
                         commit everything into the <org>/demo repository; Actions takes it from there
  outpost demo status    the latest workflow runs, and what each infrastructure is serving

The workflow has one `build` job (runs on a node with Docker, pushes the image into Gitea's registry)
and one deploy job per infrastructure in servers.yml. Jobs run on the Outpost runners, which are the
nodes themselves, so nothing in the pipeline can reach anything but Gitea. The page the site serves
measures that, at build time and on every request.
"""

import json
from pathlib import Path

from .config import Servers
from .gitea.connect import connect, gitea_info
from .paths import Platform
from .ssh import Host

NAME = "demo"
TEMPLATE = Path(__file__).parent / "templates" / "platform" / NAME
PORTS = {"compose": 8000, "swarm": 8080, "k3s": 30080}   # one port per type, so one node can host several
IGNORE = {".git", "__pycache__", ".gitea"}

NEEDS_YML = """\
# The demo project in demo/. Delete this file and the folder once you have your own projects.
name: demo
python: "3.12"
images: [python:3.12-slim]
pypi:
  - file: ../demo/requirements.txt
apt:
  distro: bookworm
  packages: [curl]
"""

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
    """The deploy workflow for these infrastructures (name -> type). `user` owns PACKAGE_TOKEN."""
    image = f"{gitea_name}/{org}/{NAME}"
    out = [
        "# Written by `outpost demo push` from servers.yml. Edit servers.yml and push again to regenerate.",
        "# Every job runs on an Outpost runner, which is a sealed node: Gitea is the only server it can reach.",
        f"name: deploy\non:\n  push:\n    branches: [main]\nenv:\n  GITEA: {gitea_name}\n  ORG: {org}\n  IMAGE: {image}\n\njobs:",
        "  build:\n    name: build the image on a sealed node\n    runs-on: docker\n    steps:",
        FETCH_SOURCE.rstrip("\n"),
        "      - name: Build (apt and pip are answered by Gitea; the Dockerfile fails if the internet is reachable)",
        "        run: docker build --build-arg GITEA=$GITEA --build-arg ORG=$ORG --build-arg GIT_SHA=${{ gitea.sha }} -t $IMAGE:${{ gitea.sha }} .",
        f"      - name: Push the image into Gitea's registry\n        run: |\n          echo \"${{{{ secrets.PACKAGE_TOKEN }}}}\" | docker login $GITEA -u {user} --password-stdin\n          docker push $IMAGE:${{{{ gitea.sha }}}}",
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
    return template.replace("{name}", name).replace("{port}", str(port)).rstrip("\n")


def ensure_local(platform: Platform) -> Path:
    """demo/ and needs/demo.yml exist in the platform folder (older platforms get them now)."""
    folder = platform.root / NAME
    if not folder.exists():
        import shutil
        shutil.copytree(TEMPLATE, folder)
    needs = platform.needs_dir / f"{NAME}.yml"
    if not needs.exists():
        needs.write_text(NEEDS_YML)
    return folder


def project_files(folder: Path) -> dict[str, bytes]:
    """Relative path -> content, for everything in demo/ except caches and the generated workflow."""
    files = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if path.is_file() and not (set(rel.parts) & IGNORE):
            files[rel.as_posix()] = path.read_bytes()
    return files


def urls(platform: Platform, servers: Servers) -> dict[str, dict]:
    """Per infrastructure: the node, the port and the URL you open, from what setup recorded."""
    out = {}
    for name, infra in servers.infrastructures.items():
        state = platform.read_state(f"infra-{name}") or {}
        node = infra.nodes[0]
        ip = ((state.get("nodes") or {}).get(node) or {}).get("ip")
        out[name] = {"type": infra.type, "node": node, "ip": ip, "port": PORTS[infra.type],
                     "url": f"http://{ip}:{PORTS[infra.type]}" if ip else None, "set_up": bool(state)}
    return out


def push(platform: Platform, servers: Servers, log=print) -> dict:
    folder = ensure_local(platform)
    info = gitea_info(platform, servers)
    ready = {n: i.type for n, i in servers.infrastructures.items() if platform.read_state(f"infra-{n}")}
    if not ready:
        raise RuntimeError("No infrastructure has been set up yet. Run `outpost infra setup <name>` first; the workflow deploys to the ones that are.")
    files = project_files(folder)
    files[".gitea/workflows/deploy.yml"] = workflow(info.name, info.org, ready, servers.gitea.user).encode()
    (folder / ".gitea" / "workflows").mkdir(parents=True, exist_ok=True)
    (folder / ".gitea" / "workflows" / "deploy.yml").write_bytes(files[".gitea/workflows/deploy.yml"])

    with connect(platform, servers) as client:
        if client.ensure_repo(info.org, NAME, "Outpost demo: built and deployed with no internet access"):
            log(f"created repository {info.org}/{NAME}")
        client.set_secret(info.org, NAME, "PACKAGE_TOKEN", _package_token(platform, servers, client))
        existing = client.repo_files(info.org, NAME)
        changed = {p: c for p, c in files.items() if p not in existing or _blob_sha(c) != existing[p]}
        if not changed:
            log("nothing changed since the last push")
            commit = None
        else:
            result = client.commit_files(info.org, NAME, changed, f"outpost demo push: {len(changed)} file{'s' if len(changed) > 1 else ''}", existing)
            commit = (result.get("commit") or {}).get("sha")
            log(f"committed {len(changed)} file{'s' if len(changed) > 1 else ''} to {info.org}/{NAME} ({(commit or '')[:10]})")
        runners = [r["name"] for r in client.runners()]
    missing = [n for n in ready if servers.infrastructures[n].nodes[0] not in runners]
    if missing:
        log(f"warning: no runner registered for {', '.join(missing)}; run `outpost infra setup <name>` again to install it")

    where = urls(platform, servers)
    base = f"https://{info.ip}/{info.org}/{NAME}"
    log(f"\nWatch the workflow:  {base}/actions")
    for name in ready:
        log(f"  {name:10} {where[name]['url'] or '(node ip unknown)'}   after the '{name}' job is green")
    result = {"commit": commit, "repo": f"{info.org}/{NAME}", "actions_url": f"{base}/actions", "infras": {n: where[n] for n in ready}}
    platform.write_state("demo", result)
    return result


def _package_token(platform: Platform, servers: Servers, client) -> str:
    """A token that may only push packages, kept in secrets/gitea-package-token. Made again when this
    Gitea does not know it (the lab was recreated, Gitea was reinstalled)."""
    password = platform.read_secret("gitea-admin-password")
    if not password:
        raise RuntimeError("secrets/gitea-admin-password is missing; it is needed to create and check the package token. Run `outpost gitea setup`.")
    token = platform.read_secret("gitea-package-token")
    if token and token[-8:] in {t.get("token_last_eight") for t in client.tokens(servers.gitea.user, password)}:
        return token
    import time
    token = client.create_token(servers.gitea.user, password, f"outpost-packages-{int(time.time())}", ["write:package", "read:repository"])
    platform.write_secret("gitea-package-token", token)
    return token


def _blob_sha(content: bytes) -> str:
    import hashlib
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def status(platform: Platform, servers: Servers) -> dict:
    """Latest workflow runs with their jobs, and what each node serves on the demo port."""
    info = gitea_info(platform, servers)
    with connect(platform, servers) as client:
        runs = []
        for run in client.workflow_runs(info.org, NAME, limit=3):
            jobs = client.workflow_jobs(info.org, NAME, run["id"])
            runs.append({"id": run["id"], "sha": run.get("head_sha", "")[:10], "status": run.get("status"), "conclusion": run.get("conclusion"),
                         "url": run.get("html_url", "").replace(f"https://{info.name}", f"https://{info.ip}"), "when": run.get("created_at"),
                         "jobs": [{"name": j["name"], "status": j.get("status"), "conclusion": j.get("conclusion")} for j in jobs]})
    serving = {}
    for name, where in urls(platform, servers).items():
        if not where["set_up"]:
            continue
        out = Host(where["node"]).run(f"curl -fsS --max-time 5 http://127.0.0.1:{where['port']}/healthz", check=False)
        try:
            health = json.loads(out.stdout) if out.ok else None
        except ValueError:
            health = None
        serving[name] = {**where, "health": health}
    result = {"repo": f"{info.org}/{NAME}", "actions_url": f"https://{info.ip}/{info.org}/{NAME}/actions", "runs": runs, "serving": serving}
    platform.write_state("demo-status", result)   # the proof report reads it; no SSH needed there
    return result
