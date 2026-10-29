"""One server: run commands on it, and copy files to and from it.

Every method ends up in one of two places, at the bottom of Host:
    _run_with_ssh()    runs a command with the ssh program       (run, write, ip, facts)
    _copy_with_scp()   copies a file with the scp program        (put, get)
"""

import logging
import shlex
import subprocess
import time
from pathlib import Path

from outpost.ssh.commands import (
    ADDRESS_ON_DEFAULT_ROUTE,
    FIRST_ADDRESS,
    PRINT_FACTS,
    all_of,
    first_of,
    print_owner_and_permissions,
)
from outpost.ssh.errors import CommandFailed, Result, SshError, Unreachable
from outpost.ssh.markers import command_finished, decode, exit_code, ssh_logged_in, without_markers, wrap_with_markers
from outpost.ssh.options import ssh_options
from outpost.ssh.permissions import permissions_in_words_from_stat, permissions_in_words_of_local

logger = logging.getLogger(__name__)


class Host:
    """One server, reached by its name in your ssh config. Nothing connects until you call a method;
    each call opens its own ssh connection."""

    def __init__(self, alias: str, connect_timeout: int = 20, config_file: Path | None = None):
        """
        Args:
            alias: the server's name in your ssh config, such as "gitea-1".
            connect_timeout: seconds to wait for the server to answer before giving up.
            config_file: the ssh config file to use (see choose_config_file); None means ~/.ssh/config.
        """
        self.alias = alias
        self.connect_timeout = connect_timeout
        self.config_file = config_file

    def __repr__(self) -> str:
        return f"Host({self.alias})"

    # -- public ---------------------------------------------------------------------------

    def run(self, script: str, raise_on_failure: bool = False, timeout: int = 1800) -> Result:
        """Run a bash script on the server, as root.

        The script is sent to bash on stdin, so it can have many lines and any quotes.
        `set -euo pipefail` is added at the top, so it stops at the first command that fails.

        Args:
            script: the bash script.
            raise_on_failure: if True, a failing script raises CommandFailed instead of returning.
            timeout: seconds before giving up on the script (default 30 minutes).

        Returns:
            The Result. Check result.ok to see if it worked.

        Raises:
            Unreachable: ssh could not log in, or the connection broke.
            CommandFailed: only with raise_on_failure=True, if the script failed.
        """
        # set -e: stop at the first failing command. -u: stop on an unset variable.
        # -o pipefail: in `a | b`, fail if `a` fails too, not only `b`.
        script_on_stdin = ("set -euo pipefail\n" + script).encode()
        # bash -s: read the script from stdin.
        result = self._run_with_ssh("bash -s", script_on_stdin, label=_first_line(script), timeout=timeout)
        if raise_on_failure and not result.ok:
            output = result.stderr.strip() or result.stdout.strip()
            raise CommandFailed(f"[{self.alias}] command failed (exit {result.returncode}):\n{output}", result)
        return result

    def write(self, path: str, content: str | bytes, mode: str = "0644") -> None:
        """Create or replace a file on the server, owned by root. Makes its folder if needed.

        Args:
            path: where to write it on the server.
            content: what to put in it.
            mode: its permissions, as chmod takes them. Each digit is owner, group, others;
                read = 4, write = 2, execute = 1. "0644" = owner read+write, others read.
                "0600" = only the owner (for secrets). "0755" = for programs.

        Raises:
            Unreachable: ssh could not log in, or the connection broke.
            CommandFailed: the file could not be written.
        """
        content_on_stdin = content.encode() if isinstance(content, str) else content
        folder, file = shlex.quote(str(Path(path).parent)), shlex.quote(path)  # quote: safe with spaces
        # The content takes stdin, so these steps go on the command line, as `bash -c '...'`.
        save_stdin_to_file = all_of(
            [
                f"mkdir -p {folder}",  # -p: also make parent folders; no error if it exists
                f"cat > {file}",  # cat copies stdin (the content) into the file
                f"chmod {mode} {file}",
                print_owner_and_permissions(file),  # for the log below
            ]
        )
        result = self._run_with_ssh(
            f"bash -c {shlex.quote(save_stdin_to_file)}", content_on_stdin, label=f"write {path}"
        )
        if not result.ok:
            raise CommandFailed(f"[{self.alias}] could not write {path}: {result.stderr.strip()}", result)
        logger.debug("[%s] wrote %s: %s", self.alias, path, permissions_in_words_from_stat(result.stdout))

    def put(self, local: Path, remote: str, mode: str = "0644", via: str = "/tmp") -> None:
        """Upload a file from this machine to the server, owned by root.

        It takes two steps, because scp logs in as your ssh user, not root, and that user may not
        be allowed to write to `remote`'s folder (such as /usr/local/bin):
            1. scp copies the file into `via`, where any user may write.
            2. root moves it to `remote`.

        Args:
            local: the file on this machine.
            remote: where to put it on the server.
            mode: its permissions; see write().
            via: the folder for step 1. Use "/var/tmp" for big files: /tmp is often small,
                because it is kept in memory.

        Raises:
            Unreachable: ssh could not log in, or the connection broke (in step 2).
            CommandFailed: scp failed (step 1), or the move failed (step 2).
        """
        staged = f"{via}/outpost-{local.name}"
        self._copy_with_scp(str(local), f"{self.alias}:{staged}", label=local.name)  # step 1
        folder, staged, target = shlex.quote(str(Path(remote).parent)), shlex.quote(staged), shlex.quote(remote)
        move_into_place = all_of(  # step 2
            [
                f"mkdir -p {folder}",
                f"mv {staged} {target}",
                f"chmod {mode} {target}",
                print_owner_and_permissions(target),  # for the log below
            ]
        )
        moved = self.run(move_into_place, raise_on_failure=True)
        logger.debug("[%s] put %s: %s", self.alias, remote, permissions_in_words_from_stat(moved.stdout))

    def get(self, remote: str, local: Path) -> Path:
        """Download a file from the server to this machine.

        scp logs in as your ssh user, not root, so that user must be allowed to read the file.

        Args:
            remote: the file on the server.
            local: where to save it here. Its folder is made if needed.

        Returns:
            `local`.

        Raises:
            CommandFailed: scp failed (the server is down, or the file is missing or not readable).
        """
        local.parent.mkdir(parents=True, exist_ok=True)
        self._copy_with_scp(f"{self.alias}:{remote}", str(local), label=remote)
        logger.debug("[%s] got %s as %s: %s", self.alias, remote, local, permissions_in_words_of_local(local))
        return local

    def ip(self) -> str:
        """The server's IP address, for other machines to reach it.

        Raises:
            SshError: if no address was found; then set it in servers.yml.
        """
        found = self.run(first_of([FIRST_ADDRESS, ADDRESS_ON_DEFAULT_ROUTE]), raise_on_failure=True).stdout.split()
        if not found:
            raise SshError(f"[{self.alias}] could not detect an IP address; set it in servers.yml")
        return found[0]

    def facts(self) -> dict:
        """Basic facts about the server, in one ssh call.

        Returns:
            For example {"id": "ubuntu", "id_like": "debian", "version_id": "24.04",
            "codename": "noble", "arch": "x86_64", "ip": "10.0.0.5", "hostname": "node-1"}.
            Setup uses "id" and "id_like" to pick the package manager: apt, apk or dnf.
        """
        facts = {}
        for line in self.run(PRINT_FACTS, raise_on_failure=True).stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)  # split at the first "=" only: values may contain "="
                facts[key] = value
        return facts

    # -- private: the only two places that start the ssh and scp programs -----------------

    def _run_with_ssh(self, command: str, stdin: bytes, label: str, timeout: int | None = None) -> Result:
        """Run `command` on the server as root, and tell apart "ssh failed" from "the command failed".

        Args:
            command: the command line to run on the server.
            stdin: what to send to the command's standard input.
            label: a short name for the command, for the logs and errors.
            timeout: seconds before giving up; None waits forever.

        Returns:
            The command's Result, with the marker lines removed from stderr.

        Raises:
            Unreachable: ssh could not log in, or the connection broke before the command finished.
        """
        started = time.monotonic()
        completed = subprocess.run(
            ["ssh", *ssh_options(self.connect_timeout, self.config_file), self.alias, wrap_with_markers(command)],
            input=stdin,
            capture_output=True,
            timeout=timeout,
        )
        seconds = time.monotonic() - started
        stdout, stderr = decode(completed.stdout), decode(completed.stderr)

        # Read the markers (see markers.py).
        if not ssh_logged_in(stderr):
            logger.debug(
                "[%s] %s: could not connect (ssh exit %d, %.1fs)", self.alias, label, completed.returncode, seconds
            )
            raise Unreachable(
                f"[{self.alias}] cannot connect over ssh (ssh exit {completed.returncode}): "
                f"{stderr.strip() or 'no answer'}"
            )
        if not command_finished(stderr):
            logger.debug(
                "[%s] %s: connection lost (ssh exit %d, %.1fs)", self.alias, label, completed.returncode, seconds
            )
            raise Unreachable(
                f"[{self.alias}] the connection was lost during `{label}` (ssh exit {completed.returncode}): "
                f"{without_markers(stderr).strip()[-300:]}"
            )

        code = exit_code(stderr)
        logger.debug("[%s] %s: command exit %d (%.1fs)", self.alias, label, code, seconds)
        return Result(code, stdout, without_markers(stderr))

    def _copy_with_scp(self, source: str, target: str, label: str) -> None:
        """Copy a file with scp. The server side is written "alias:/path".

        scp can't use the markers, and exits with 1 both when the server is down and when the file
        is missing. We can't tell those apart, so every scp failure is a CommandFailed.

        Raises:
            CommandFailed: scp failed.
        """
        started = time.monotonic()
        completed = subprocess.run(
            ["scp", *ssh_options(self.connect_timeout, self.config_file), "-q", source, target],  # -q: no progress bar
            capture_output=True,
        )
        logger.debug(
            "[%s] scp %s: scp exit %d (%.1fs)", self.alias, label, completed.returncode, time.monotonic() - started
        )
        if completed.returncode != 0:
            stderr = decode(completed.stderr)
            raise CommandFailed(
                f"[{self.alias}] scp {label} failed: {stderr.strip()}", Result(completed.returncode, "", stderr)
            )


def _first_line(script: str) -> str:
    """The script's first non-empty line, cut to 70 characters: its name in the logs."""
    first = next((line.strip() for line in script.splitlines() if line.strip()), "")
    return first if len(first) <= 70 else first[:67] + "..."
