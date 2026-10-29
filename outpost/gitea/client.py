"""Gitea's REST API: version, organisations, repositories, tokens, secrets, runners and workflow runs.

    client = GiteaClient(http, "pkgs", public_url="https://gitea.internal")
    client.ensure_org("pkgs")
    client.packages.upload_pypi(Path("box/pypi/requests-2.31.0-py3-none-any.whl"))   # see packages.py

`gitea.connect(system)` makes one, through an ssh tunnel.
"""

import base64
import json
from urllib.parse import quote

from outpost.gitea.packages import GiteaPackages
from outpost.http import Http


class GiteaClient:
    """One Gitea, logged in, with its organisation. `client.packages` reaches its package registries."""

    def __init__(self, http: Http, org: str, public_url: str | None = None):
        """
        Args:
            http: logged in to Gitea (Basic auth with the API token).
            org: the organisation Outpost keeps everything in.
            public_url: Gitea's URL as the nodes see it (default: http's base URL).
        """
        self.http = http
        self.org = org
        self.public_url = (public_url or http.base).rstrip("/")
        self.packages = GiteaPackages(http, org, self.public_url)

    # -- basics -------------------------------------------------------------
    def version(self) -> str:
        """Gitea's version, such as "28.2.0"."""
        return self.http.get("/api/v1/version", ok=(200,)).json()["version"]

    def ensure_org(self, name: str) -> bool:
        """Create a public organisation if it does not exist. Returns True when created."""
        if self.http.get(f"/api/v1/orgs/{quote(name)}").status == 200:
            return False
        body = json.dumps({"username": name, "visibility": "public", "repo_admin_change_team_access": True}).encode()
        self.http.request("POST", "/api/v1/orgs", data=body, headers={"Content-Type": "application/json"}, ok=(201,))
        return True

    # -- repositories and Actions (used by the demo) ------------------------
    def ensure_repo(self, owner: str, name: str, description: str = "") -> bool:
        """Create a public repository under an organisation, with Actions on. True when created."""
        if self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(name)}").status == 200:
            return False
        self._json(
            "POST",
            f"/api/v1/orgs/{quote(owner)}/repos",
            {"name": name, "description": description, "auto_init": False, "default_branch": "main", "private": False},
            ok=(201,),
        )
        self._json("PATCH", f"/api/v1/repos/{quote(owner)}/{quote(name)}", {"has_actions": True, "has_packages": True})
        return True

    def repo_files(self, owner: str, repo: str) -> dict[str, str]:
        """path -> blob sha of every file on the default branch (empty for an empty repository)."""
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/git/trees/HEAD?recursive=true&per_page=1000")
        if resp.status != 200:
            return {}
        return {e["path"]: e["sha"] for e in resp.json().get("tree") or [] if e.get("type") == "blob"}

    def commit_files(
        self, owner: str, repo: str, files: dict[str, bytes], message: str, existing: dict[str, str]
    ) -> dict:
        """One commit on main that creates or updates `files` (path -> content). Returns Gitea's answer."""
        ops = [
            {
                "operation": "update" if path in existing else "create",
                "path": path,
                "content": base64.b64encode(content).decode(),
                **({"sha": existing[path]} if path in existing else {}),
            }
            for path, content in sorted(files.items())
        ]
        resp = self._json(
            "POST",
            f"/api/v1/repos/{quote(owner)}/{quote(repo)}/contents",
            {"branch": "main", "message": message, "files": ops},
            ok=(201,),
        )
        return resp.json()

    def create_token(self, user: str, password: str, name: str, scopes: list[str]) -> str:
        """A new API token with just these scopes. Gitea only issues tokens against a password, so this
        is the one call that uses the admin password instead of the admin token."""
        saved = self.http.basic
        self.http.basic = (user, password)
        try:
            resp = self._json(
                "POST", f"/api/v1/users/{quote(user)}/tokens", {"name": name, "scopes": scopes}, ok=(201,)
            )
        finally:
            self.http.basic = saved
        return resp.json()["sha1"]

    def tokens(self, user: str, password: str) -> list[dict]:
        """The user's API tokens (name and last eight characters), listed with the password like create_token."""
        saved = self.http.basic
        self.http.basic = (user, password)
        try:
            return self._json("GET", f"/api/v1/users/{quote(user)}/tokens").json()
        finally:
            self.http.basic = saved

    def set_secret(self, owner: str, repo: str, name: str, value: str) -> None:
        """An Actions secret on a repository, available to its workflows as ${{ secrets.NAME }}."""
        self._json(
            "PUT",
            f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/secrets/{quote(name)}",
            {"data": value},
            ok=(201, 204),
        )

    def runner_registration_token(self, org: str | None = None) -> str:
        """A token that registers a runner for the organisation, so every repository in it can use the
        nodes. Organisation level needs only write:organization, where the instance level needs admin."""
        resp = self._json(
            "POST", f"/api/v1/orgs/{quote(org or self.org)}/actions/runners/registration-token", ok=(200, 201)
        )
        return (resp.json() or {}).get("token") or resp.headers.get("token") or resp.headers.get("Token")  # pyright: ignore[reportReturnType]  Gitea may send no token; caller assumes one

    def runners(self, org: str | None = None) -> list[dict]:
        """The Actions runners registered on the organisation."""
        resp = self.http.get(f"/api/v1/orgs/{quote(org or self.org)}/actions/runners")
        return (resp.json() or {}).get("runners", []) if resp.status == 200 else []

    def workflow_runs(self, owner: str, repo: str, limit: int = 5) -> list[dict]:
        """The latest Actions workflow runs of a repository, newest first."""
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/runs?limit={limit}")
        return (resp.json() or {}).get("workflow_runs", []) if resp.status == 200 else []

    def workflow_jobs(self, owner: str, repo: str, run_id: int) -> list[dict]:
        """The jobs of one workflow run."""
        resp = self.http.get(f"/api/v1/repos/{quote(owner)}/{quote(repo)}/actions/runs/{run_id}/jobs")
        return (resp.json() or {}).get("jobs", []) if resp.status == 200 else []

    # -- one JSON request ----------------------------------------------------
    def _json(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201, 204)):
        """Send body as JSON; a status not in ok raises HttpError."""
        data = json.dumps(body).encode() if body is not None else None
        return self.http.request(method, path, data=data, headers={"Content-Type": "application/json"}, ok=ok)
