"""Read the smoke checks in checks/*.yml."""

from pathlib import Path

import yaml

from outpost.checks.check import REQUIREMENTS, Check
from outpost.config import ConfigError


def load_checks(checks_dir: Path) -> list[Check]:
    """Every check in checks_dir, sorted by file name; none when the folder does not exist.

    Raises:
        ConfigError: a check has no steps, or `requires:` names something unknown.
    """
    if not checks_dir.exists():
        return []
    return [_read_check(path, checks_dir / path.stem) for path in sorted(checks_dir.glob("*.yml"))]


def _read_check(path: Path, folder: Path) -> Check:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict) or not data.get("steps"):
        raise ConfigError(f"{path}: a check needs a `steps:` list")
    return Check(
        name=str(data.get("name") or path.stem),
        description=str(data.get("description", "")),
        infra=[str(i) for i in data.get("infra") or []],
        steps=[_read_step(step) for step in data["steps"]],
        context=folder if folder.is_dir() else None,
        requires=_read_requires(path, data.get("requires") or []),
    )


def _read_step(step) -> dict:
    """A step is a command, or {run: command, expect: text}."""
    if isinstance(step, dict):
        return {"run": str(step["run"]), "expect": step.get("expect")}
    return {"run": str(step), "expect": None}


def _read_requires(path: Path, requires) -> list[str]:
    requires = [requires] if isinstance(requires, str) else requires
    unknown = [str(r) for r in requires if str(r) not in REQUIREMENTS] if isinstance(requires, list) else []
    if not isinstance(requires, list) or unknown:
        raise ConfigError(
            f"{path}: `requires:` may only list {', '.join(REQUIREMENTS)} (got {', '.join(unknown) or requires!r})"
        )
    return [str(r) for r in requires]
