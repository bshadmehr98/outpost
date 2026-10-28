"""The workspace: the folder made by `outpost init`. One workspace is one air-gapped system.

    workspace = Workspace.open("my-system")       # it must have a servers.yml
    workspace.box_dir                             # my-system/box
    workspace.write_state(STATE_FETCH, {...})     # my-system/state/fetch.json
    workspace.read_secret(GITEA_TOKEN)            # my-system/secrets/gitea-token, or None

The layout:

    my-system/
      servers.yml     which servers play which role
      mirrors.yml     where fetch downloads from (optional)
      needs/          what your projects need: images, pip, npm, apt, files
      checks/         smoke checks to run on an infrastructure
      demo/           the example project
      box/            what fetch downloaded
      pki/            public certificates (safe to commit)
      secrets/        private keys, passwords and tokens (never commit)
      state/          what Outpost did last, one JSON file per step (safe to commit)
        hosts/          what each server ran when last asked
        runs/           the logs of commands started from the dashboard
      backups/        Gitea backups (never commit: they hold Gitea's keys)
      lab/            the lab's ssh key and config, made by `outpost lab up`

Every name in the layout is a constant here; code joins it to the root through a property
(`workspace.box_dir`), never by hand.
"""

import json
import os
import shutil
from pathlib import Path

from outpost.config import ConfigError

TEMPLATE_DIR = Path(__file__).parent / "templates" / "workspace"  # what `outpost init` copies

# -- the layout -------------------------------------------------------------------
SERVERS_FILE = "servers.yml"
MIRRORS_FILE = "mirrors.yml"
NEEDS_DIR = "needs"
CHECKS_DIR = "checks"
DEMO_DIR = "demo"
BOX_DIR = "box"
PKI_DIR = "pki"
SECRETS_DIR = "secrets"
STATE_DIR = "state"
BACKUPS_DIR = "backups"
LAB_DIR = "lab"
HOSTS_STATE_DIR = "hosts"  # inside state/
RUNS_DIR = "runs"  # inside state/
REPORT_FILE = "report.html"  # inside state/

# -- files in pki/ (public) and secrets/ (private, mode 600) -----------------------
CA_CERT = "ca.crt"
GITEA_CERT = "gitea.crt"
BOX_PUBLIC_KEY = "box.pub"
CA_KEY = "ca.key"
GITEA_KEY = "gitea.key"
BOX_SIGNING_KEY = "box.key"
GITEA_TOKEN = "gitea-token"
GITEA_ADMIN_PASSWORD = "gitea-admin-password"
GITEA_PACKAGE_TOKEN = "gitea-package-token"

# -- state files: state/<name>.json ------------------------------------------------
STATE_FETCH = "fetch"
STATE_UPLOAD = "upload"
STATE_GITEA = "gitea"
STATE_PROXY = "proxy"
STATE_BACKUP = "backup"
STATE_DEMO = "demo"
STATE_DEMO_STATUS = "demo-status"


def infra_state(infra_name: str) -> str:
    """The state name `infra setup` writes for one infrastructure."""
    return f"infra-{infra_name}"


def verify_state(infra_name: str) -> str:
    """The state name `infra verify` writes for one infrastructure."""
    return f"verify-{infra_name}"


def check_state(infra_name: str) -> str:
    """The state name `outpost check` writes for one infrastructure."""
    return f"check-{infra_name}"


class Workspace:
    """One workspace folder: where each file is, and the state and secrets in it."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    @classmethod
    def open(cls, root: str | Path) -> "Workspace":
        """The workspace at root, which must have a servers.yml.

        Raises:
            ConfigError: there is no servers.yml in root.
        """
        workspace = cls(root)
        if not workspace.exists():
            raise ConfigError(f"No servers.yml in {workspace.root}. Run `outpost init` here, or pass --dir.")
        return workspace

    @classmethod
    def create(cls, target: str | Path) -> "Workspace":
        """Copy the template workspace into target. Files that are already there are kept."""
        workspace = cls(target)
        workspace.root.mkdir(parents=True, exist_ok=True)
        for item in TEMPLATE_DIR.iterdir():
            dest = workspace.root / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            elif not dest.exists():
                shutil.copy2(item, dest)
        return workspace

    def exists(self) -> bool:
        """True when the folder has a servers.yml."""
        return self.servers_file.exists()

    # -- folders and files ---------------------------------------------------------
    @property
    def servers_file(self) -> Path:
        return self.root / SERVERS_FILE

    @property
    def mirrors_file(self) -> Path:
        return self.root / MIRRORS_FILE

    @property
    def needs_dir(self) -> Path:
        return self.root / NEEDS_DIR

    @property
    def checks_dir(self) -> Path:
        return self.root / CHECKS_DIR

    @property
    def demo_dir(self) -> Path:
        return self.root / DEMO_DIR

    @property
    def box_dir(self) -> Path:
        return self.root / BOX_DIR

    @property
    def pki_dir(self) -> Path:
        return self.root / PKI_DIR

    @property
    def secrets_dir(self) -> Path:
        return self.root / SECRETS_DIR

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def backups_dir(self) -> Path:
        return self.root / BACKUPS_DIR

    @property
    def lab_dir(self) -> Path:
        return self.root / LAB_DIR

    @property
    def hosts_state_dir(self) -> Path:
        return self.state_dir / HOSTS_STATE_DIR

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / RUNS_DIR

    @property
    def report_file(self) -> Path:
        return self.state_dir / REPORT_FILE

    @property
    def ca_cert(self) -> Path:
        return self.pki_dir / CA_CERT

    @property
    def box_public_key(self) -> Path:
        return self.pki_dir / BOX_PUBLIC_KEY

    # -- state: small JSON files about what happened last ----------------------------
    def read_state(self, name: str, default=None):
        """The content of state/<name>.json, or default when the file is missing."""
        path = self._state_file(name)
        if not path.exists():
            return default
        return json.loads(path.read_text())

    def write_state(self, name: str, data) -> Path:
        """Write data to state/<name>.json, sorted and indented, so a diff of it reads well."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_file(name)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return path

    def state_names(self) -> list[str]:
        """The names of every state file, sorted (state/fetch.json is "fetch")."""
        if not self.state_dir.exists():
            return []
        return [path.stem for path in sorted(self.state_dir.glob("*.json"))]

    def state_mtime(self, name: str) -> float | None:
        """When state/<name>.json was last written, in seconds since the epoch, or None."""
        path = self._state_file(name)
        return path.stat().st_mtime if path.exists() else None

    # -- secrets: private keys, passwords and tokens, mode 600 ---------------------------
    def read_secret(self, name: str) -> str | None:
        """The content of secrets/<name> without surrounding whitespace, or None when it is missing."""
        path = self.secrets_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def write_secret(self, name: str, value: str) -> Path:
        """Write secrets/<name>, readable by its owner only."""
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        path = self.secrets_dir / name
        path.write_text(value.strip() + "\n")
        os.chmod(path, 0o600)
        return path

    def _state_file(self, name: str) -> Path:
        return self.state_dir / f"{name}.json"
