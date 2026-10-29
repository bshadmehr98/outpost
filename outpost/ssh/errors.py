"""What Host's methods give back: a Result, or one of these errors.

SshError                 base class: `except SshError` catches both below
├── Unreachable          ssh failed: the command did not run, or did not finish
└── CommandFailed        the command ran and failed; `.result` has its output
"""

from dataclasses import dataclass


@dataclass
class Result:
    """What a command printed, and how it ended.

    Attributes:
        returncode: the command's exit code. 0 means it worked; anything else means it failed.
        stdout: what it printed to standard output.
        stderr: what it printed to standard error (without Outpost's marker lines).
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command worked (exit code 0)."""
        return self.returncode == 0


class SshError(Exception):
    """Base class for every error in outpost.ssh. Catch this to catch them all."""


class Unreachable(SshError):  # noqa: N818 public name of outpost.ssh, chosen to read as a sentence; rename is a refactor decision
    """ssh failed, not the command.

    Raised when ssh could not log in (the server is down, the address is wrong, the key was refused,
    or it timed out), or when the connection broke before the command finished.
    It is raised even with raise_on_failure=False, because there is no Result to give back.
    """


class CommandFailed(SshError):  # noqa: N818 public name of outpost.ssh, chosen to read as a sentence; rename is a refactor decision
    """The command ran on the server, and exited with a code other than 0.

    Attributes:
        result: the failed command's Result: its exit code, stdout and stderr.
    """

    def __init__(self, message: str, result: Result):
        super().__init__(message)
        self.result = result
