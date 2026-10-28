"""The command line's output. Commands print through say(); the modules below them get it as `log`."""

import sys


def say(message: str = "") -> None:
    """Print one line for the user, right away (the dashboard streams it)."""
    print(message, flush=True)


def say_error(message: str) -> None:
    """Print `error: <message>` on stderr."""
    print(f"error: {message}", file=sys.stderr)
