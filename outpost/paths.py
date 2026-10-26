"""The platform folder. Everything Outpost knows about your setup lives in one directory.

    my-platform/
      servers.yml     which SSH hosts play which role
      mirrors.yml     where to download from (optional)
      needs/          what your projects need: images, pip, npm, apt, files
      checks/         smoke tests to run on an infrastructure
      box/            downloaded artifacts, produced by `outpost fetch`
      pki/            public certificates (safe to commit)
      secrets/        private keys and tokens (never commit)
      state/          what Outpost did last (safe to commit or ignore)
"""

import json
import os
from pathlib import Path


class Platform:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    # -- layout -------------------------------------------------------------
    @property
    def servers_file(self) -> Path:
        return self.root / "servers.yml"

    @property
    def mirrors_file(self) -> Path:
        return self.root / "mirrors.yml"

    @property
    def needs_dir(self) -> Path:
        return self.root / "needs"

    @property
    def checks_dir(self) -> Path:
        return self.root / "checks"

    @property
    def box_dir(self) -> Path:
        return self.root / "box"

    @property
    def pki_dir(self) -> Path:
        return self.root / "pki"

    @property
    def secrets_dir(self) -> Path:
        return self.root / "secrets"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    def exists(self) -> bool:
        return self.servers_file.exists()

    # -- state: small JSON files describing what happened last ----------------
    def read_state(self, name: str, default=None):
        path = self.state_dir / f"{name}.json"
        if not path.exists():
            return default
        return json.loads(path.read_text())

    def write_state(self, name: str, data) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return path

    # -- secrets: tokens and private keys, mode 600, gitignored --------------
    def read_secret(self, name: str) -> str | None:
        path = self.secrets_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def write_secret(self, name: str, value: str) -> Path:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        path = self.secrets_dir / name
        path.write_text(value.strip() + "\n")
        os.chmod(path, 0o600)
        return path
