"""Smoke checks: small scenarios that prove an infrastructure really builds and runs offline.

    checks/hello-python.yml
    checks/hello-python/Dockerfile        optional folder with the same name, copied to the node

    name: hello-python
    description: Build an image with apt + pip from Gitea, run it, confirm the internet is blocked.
    infra: [compose, swarm]              # which infrastructure types this applies to (default: all)
    requires: [proxy]                    # optional: skipped, with a message, unless the egress proxy is set up
    steps:
      - run: docker build --build-arg GITEA=$GITEA --build-arg ORG=$ORG -t check-hello .
      - run: docker run --rm check-hello
        expect: Hello from Outpost

Steps run on the first node, in the copied folder, as root, with $GITEA and $ORG set, and with an egress
proxy also $PROXY_URL, $PROXY_ALLOWED (a name it lets through) and $PROXY_REFUSED (one it must refuse).
A step passes when its command exits 0 and, if `expect:` is given, its output contains that text.
"""

import shlex
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import ConfigError

REMOTE_ROOT = "/tmp/outpost-checks"
REQUIREMENTS = ("proxy",)


@dataclass
class Check:
    name: str
    description: str = ""
    infra: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    context: Path | None = None
    requires: list[str] = field(default_factory=list)  # "proxy": only where the egress proxy is set up

    def applies_to(self, infra_type: str) -> bool:
        return not self.infra or infra_type in self.infra


def load_checks(checks_dir: Path) -> list[Check]:
    checks = []
    if not checks_dir.exists():
        return checks
    for path in sorted(checks_dir.glob("*.yml")):
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict) or not data.get("steps"):
            raise ConfigError(f"{path}: a check needs a `steps:` list")
        folder = checks_dir / path.stem
        requires = data.get("requires") or []
        requires = [requires] if isinstance(requires, str) else requires
        unknown = [str(r) for r in requires if str(r) not in REQUIREMENTS]
        if not isinstance(requires, list) or unknown:
            raise ConfigError(
                f"{path}: `requires:` may only list {', '.join(REQUIREMENTS)} (got {', '.join(unknown) or requires!r})"
            )
        checks.append(
            Check(
                name=str(data.get("name") or path.stem),
                description=str(data.get("description", "")),
                infra=[str(i) for i in data.get("infra") or []],
                steps=[
                    {"run": str(s["run"]), "expect": s.get("expect")}
                    if isinstance(s, dict)
                    else {"run": str(s), "expect": None}
                    for s in data["steps"]
                ],
                context=folder if folder.is_dir() else None,
                requires=[str(r) for r in requires],
            )
        )
    return checks


def _tar_folder(folder: Path) -> Path:
    with tempfile.NamedTemporaryFile(prefix="outpost-check-", suffix=".tgz", delete=False) as tmp:
        archive = Path(tmp.name)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(folder, arcname=".")
    return archive


def run_check(check: Check, host, env: dict[str, str], log=print, timeout: int = 900) -> dict:
    remote_dir = f"{REMOTE_ROOT}/{check.name}"
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    host.run(f"rm -rf {remote_dir} && mkdir -p {remote_dir}", raise_on_failure=True)
    if check.context:
        archive = _tar_folder(check.context)
        try:
            host.put(archive, f"{remote_dir}/context.tgz")
        finally:
            archive.unlink(missing_ok=True)
        host.run(
            f"tar -xzf {remote_dir}/context.tgz -C {remote_dir} && rm {remote_dir}/context.tgz", raise_on_failure=True
        )

    results = []
    ok = True
    for step in check.steps:
        result = host.run(f"export {exports}\ncd {remote_dir}\n{step['run']}", timeout=timeout)
        output = (result.stdout + result.stderr).strip()
        passed = result.ok and (step["expect"] is None or step["expect"] in output)
        results.append({"run": step["run"], "ok": passed, "output": output[-2000:]})
        log(f"  {'ok  ' if passed else 'FAIL'} {step['run']}")
        if not passed:
            ok = False
            for line in output.splitlines()[-8:]:
                log(f"       | {line}")
            break
    host.run(f"rm -rf {remote_dir}")
    return {"name": check.name, "node": host.alias, "ok": ok, "steps": results}
