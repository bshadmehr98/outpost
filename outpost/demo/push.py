"""`outpost demo push`: commit the demo and its workflow into Gitea; Gitea Actions takes it from there."""

import time
from pathlib import Path

from outpost import gitea  # gitea.connect through the package, so tests replace one name: outpost.gitea.connect
from outpost.demo.project import NAME, ensure_local, git_blob_sha, project_files
from outpost.demo.status import demo_urls
from outpost.demo.workflow import workflow
from outpost.endpoints import GiteaEndpoint, gitea_endpoint
from outpost.gitea import GiteaClient
from outpost.system import AirGappedSystem
from outpost.workspace import GITEA_ADMIN_PASSWORD, GITEA_PACKAGE_TOKEN, STATE_DEMO

WORKFLOW_FILE = ".gitea/workflows/deploy.yml"
REPO_DESCRIPTION = "Outpost demo: built and deployed with no internet access"
PACKAGE_TOKEN_SCOPES = ["write:package", "read:repository"]


def push(system: AirGappedSystem, log=print) -> dict:
    """Commit what changed in demo/, with a workflow for the infrastructures that are set up.

    Returns:
        {"commit", "repo", "actions_url", "infras"}, also written to state/demo.json.

    Raises:
        RuntimeError: no infrastructure is set up yet, or secrets/gitea-admin-password is missing.
    """
    folder = ensure_local(system.workspace)
    endpoint = gitea_endpoint(system)
    ready = {infra.name: infra.type for infra in system.set_up_infras()}
    if not ready:
        raise RuntimeError(
            "No infrastructure has been set up yet. Run `outpost infra setup <name>` first; the workflow deploys to the ones that are."
        )
    files = _files_with_workflow(folder, endpoint, ready, system.servers.gitea.user)
    with gitea.connect(system) as client:
        if client.ensure_repo(endpoint.org, NAME, REPO_DESCRIPTION):
            log(f"created repository {endpoint.org}/{NAME}")
        client.set_secret(endpoint.org, NAME, "PACKAGE_TOKEN", package_token(system, client))
        commit = _commit_changes(client, endpoint.org, files, log)
        runners = [runner["name"] for runner in client.runners()]
    _warn_about_missing_runners(system, ready, runners, log)
    return _record_push(system, endpoint, ready, commit, log)


def package_token(system: AirGappedSystem, client: GiteaClient) -> str:
    """A token that may only push packages, kept in secrets/gitea-package-token. Made again when this
    Gitea does not know it (the lab was recreated, Gitea was reinstalled).

    Raises:
        RuntimeError: secrets/gitea-admin-password is missing; Gitea issues tokens only against it.
    """
    password = system.secret(GITEA_ADMIN_PASSWORD)
    if not password:
        raise RuntimeError(
            "secrets/gitea-admin-password is missing; it is needed to create and check the package token. Run `outpost gitea setup`."
        )
    user = system.servers.gitea.user
    token = system.secret(GITEA_PACKAGE_TOKEN)
    if token and token[-8:] in {known.get("token_last_eight") for known in client.tokens(user, password)}:
        return token
    token = client.create_token(user, password, f"outpost-packages-{int(time.time())}", PACKAGE_TOKEN_SCOPES)
    system.write_secret(GITEA_PACKAGE_TOKEN, token)
    return token


def _files_with_workflow(folder: Path, endpoint: GiteaEndpoint, ready: dict[str, str], user: str) -> dict[str, bytes]:
    """demo/'s files plus the workflow for the ready infrastructures; the workflow is also written into demo/."""
    files = project_files(folder)
    files[WORKFLOW_FILE] = workflow(endpoint.name, endpoint.org, ready, user).encode()
    (folder / WORKFLOW_FILE).parent.mkdir(parents=True, exist_ok=True)
    (folder / WORKFLOW_FILE).write_bytes(files[WORKFLOW_FILE])
    return files


def _commit_changes(client: GiteaClient, org: str, files: dict[str, bytes], log) -> str | None:
    """One commit with the files that differ from the repository. Returns its sha, or None when nothing changed."""
    existing = client.repo_files(org, NAME)
    changed = {path: content for path, content in files.items() if existing.get(path) != git_blob_sha(content)}
    if not changed:
        log("nothing changed since the last push")
        return None
    count = f"{len(changed)} file{'s' if len(changed) > 1 else ''}"
    result = client.commit_files(org, NAME, changed, f"outpost demo push: {count}", existing)
    commit = (result.get("commit") or {}).get("sha")
    log(f"committed {count} to {org}/{NAME} ({(commit or '')[:10]})")
    return commit


def _warn_about_missing_runners(system: AirGappedSystem, ready: dict[str, str], runners: list[str], log) -> None:
    missing = [name for name in ready if system.infra(name).nodes[0] not in runners]
    if missing:
        log(
            f"warning: no runner registered for {', '.join(missing)}; run `outpost infra setup <name>` again to install it"
        )


def _record_push(
    system: AirGappedSystem, endpoint: GiteaEndpoint, ready: dict[str, str], commit: str | None, log
) -> dict:
    """Say where to watch the workflow and open the site, and write state/demo.json."""
    where = demo_urls(system)
    base = f"https://{endpoint.ip}/{endpoint.org}/{NAME}"
    log(f"\nWatch the workflow:  {base}/actions")
    for name in ready:
        log(f"  {name:10} {where[name]['url'] or '(node ip unknown)'}   after the '{name}' job is green")
    result = {
        "commit": commit,
        "repo": f"{endpoint.org}/{NAME}",
        "actions_url": f"{base}/actions",
        "infras": {name: where[name] for name in ready},
    }
    system.write_state(STATE_DEMO, result)
    return result
