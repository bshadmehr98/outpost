"""Did ssh fail, or did the command fail?

The problem: ssh exits with 255 when it can't log in. But a command can exit with 255 too.
So the exit code alone can't tell us which one failed.

The fix: the server prints one marker line to stderr before the command, and one after it:

    outpost-ssh: connected     <- printed first. Missing: ssh never logged in.
    ...the command runs...
    outpost-ssh: exit 3        <- printed last. Missing: the connection broke halfway.
                                                Present: 3 is the command's real exit code.

This file has both halves:
    before ssh runs:    wrap_with_markers() builds the text the server runs
    after ssh returns:  ssh_logged_in(), command_finished(), exit_code(), without_markers() read stderr
"""

import re

MARKER_CONNECTED = "outpost-ssh: connected"
MARKER_EXIT = "outpost-ssh: exit"

# "outpost-ssh: exit 3" as the very last line of stderr (\Z means "end of the text").
# It must be last: if the command itself printed something like it earlier, we ignore that.
_MARKER_EXIT_AS_LAST_LINE = re.compile(rf"{MARKER_EXIT} (\d+)\n?\Z")


# -- before: what ssh runs on the server ------------------------------------------------


def wrap_with_markers(command: str) -> str:
    """The text ssh runs on the server: `command`, as root, between the two marker lines.

    For example, wrap_with_markers("bash -s") gives:

        echo 'outpost-ssh: connected' >&2
        if [ "$(id -u)" = 0 ]; then bash -s; else sudo -n bash -s; fi
        code=$?
        echo "outpost-ssh: exit $code" >&2
        exit $code
    """
    return "\n".join(
        [
            f"echo '{MARKER_CONNECTED}' >&2",  # >&2: print to stderr, not stdout
            as_root(command),
            "code=$?",  # $? is the exit code of the command that just ran
            f'echo "{MARKER_EXIT} $code" >&2',
            "exit $code",  # exit with the command's code, so ssh's exit code matches too
        ]
    )


def as_root(command: str) -> str:
    """The shell text that runs `command` as root.

    `id -u` prints the user's number; root's is always 0. So:
      - logged in as root: run the command directly.
      - logged in as another user (for example a sudoer): run it with `sudo -n`.
        -n means "never ask for a password; fail instead", so Outpost never hangs at a prompt.
        This works only if that user may use sudo without a password (NOPASSWD in sudoers).
    """
    return f'if [ "$(id -u)" = 0 ]; then {command}; else sudo -n {command}; fi'


# -- after: reading what ssh printed ----------------------------------------------------


def decode(output: bytes) -> str:
    """ssh's output, from bytes to text. Bytes that are not valid UTF-8 become "�" instead of an error."""
    return output.decode("utf-8", "replace")


def ssh_logged_in(stderr: str) -> bool:
    """True if the first marker is there: ssh logged in and started the command."""
    return MARKER_CONNECTED + "\n" in stderr


def command_finished(stderr: str) -> bool:
    """True if the last marker is there: the command ran to the end (it may still have failed)."""
    return _MARKER_EXIT_AS_LAST_LINE.search(stderr) is not None


def exit_code(stderr: str) -> int:
    """The command's real exit code, read from the last marker. Call only if command_finished()."""
    return int(_MARKER_EXIT_AS_LAST_LINE.search(stderr).group(1))


def without_markers(stderr: str) -> str:
    """stderr with both marker lines removed: only what the command itself printed."""
    stderr = stderr.replace(MARKER_CONNECTED + "\n", "", 1)
    return _MARKER_EXIT_AS_LAST_LINE.sub("", stderr)
