"""One smoke check, as read from checks/<name>.yml."""

from dataclasses import dataclass, field
from pathlib import Path

REQUIREMENTS = ("proxy",)  # what a check may list under `requires:`


@dataclass
class Check:
    """A smoke check: steps to run on the first node of an infrastructure, and a folder to copy there."""

    name: str
    description: str = ""
    infra: list[str] = field(default_factory=list)  # the infrastructure types it applies to; empty means all
    steps: list[dict] = field(default_factory=list)  # {"run": command, "expect": text or None}
    context: Path | None = None  # checks/<name>/, copied to the node when it exists
    requires: list[str] = field(default_factory=list)  # "proxy": only where the egress proxy is set up

    def applies_to(self, infra_type: str) -> bool:
        """True when the check runs on this infrastructure type."""
        return not self.infra or infra_type in self.infra
