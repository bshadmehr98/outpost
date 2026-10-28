"""A port on this machine that leads to a port on the server.

    with Tunnel("gitea-1", 443) as tunnel:
        # here, 127.0.0.1:{tunnel.port} reaches port 443 on gitea-1
        ...
    # here, the tunnel is closed

This is how Outpost talks to Gitea's API: Gitea listens on the server, and the tunnel brings it here.
"""

import socket
import subprocess
import time

from outpost.ssh.errors import Unreachable
from outpost.ssh.markers import decode
from outpost.ssh.options import ssh_options


class Tunnel:
    """An `ssh -N -L` running in the background, while the `with` block runs.

    It works wherever `ssh gitea-1` works, even if this machine can't reach the server's address
    directly (for example, through a jump host in your ssh config).

    Attributes:
        port: the port on this machine (127.0.0.1). Set when the `with` block starts.
    """

    def __init__(self, alias: str, remote_port: int = 443, connect_timeout: int = 20):
        """
        Args:
            alias: the server's name in your ssh config.
            remote_port: the port on the server to reach.
            connect_timeout: seconds to wait for ssh to log in.
        """
        self.alias = alias
        self.remote_port = remote_port
        self.connect_timeout = connect_timeout
        self.port: int | None = None
        self._ssh: subprocess.Popen | None = None  # the ssh program, running in the background

    def __enter__(self) -> "Tunnel":
        """Start ssh, and wait until the tunnel works. Raises Unreachable if it doesn't."""
        self.port = _free_local_port()
        self._ssh = subprocess.Popen(
            [
                "ssh",
                "-N",  # run no command on the server; only forward the port
                "-o",
                "ExitOnForwardFailure=yes",  # if the port can't be forwarded, exit instead of staying up
                *ssh_options(self.connect_timeout),
                "-L",  # -L here:port:there:port
                f"127.0.0.1:{self.port}:127.0.0.1:{self.remote_port}",
                self.alias,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,  # keep ssh's errors, to show them if it fails
        )
        self._wait_until_open()
        return self

    def __exit__(self, *_exception) -> None:
        """Close the tunnel when the `with` block ends, even if it ended with an error."""
        self._close()

    def _wait_until_open(self) -> None:
        """Wait until the local port accepts connections.

        Raises:
            Unreachable: if ssh exits (it could not log in or forward), or it takes too long.
        """
        deadline = time.time() + self.connect_timeout + 5
        while time.time() < deadline:
            if self._ssh_exited():
                raise Unreachable(f"tunnel to {self.alias} failed: {decode(self._ssh.stderr.read()).strip()}")  # pyright: ignore[reportOptionalMemberAccess]  set in __enter__
            if _port_accepts_connections(self.port):  # pyright: ignore[reportArgumentType]  set in __enter__
                return
            time.sleep(0.2)
        self._close()
        raise Unreachable(f"tunnel to {self.alias} did not come up in time")

    def _ssh_exited(self) -> bool:
        """True if the ssh program has stopped. (poll() gives its exit code, or None while it runs.)"""
        return self._ssh.poll() is not None  # pyright: ignore[reportOptionalMemberAccess]  set in __enter__

    def _close(self) -> None:
        """Stop ssh: ask it to stop, and force it if it hasn't after 5 seconds."""
        if self._ssh and not self._ssh_exited():
            self._ssh.terminate()
            try:
                self._ssh.wait(5)
            except subprocess.TimeoutExpired:
                self._ssh.kill()


def _free_local_port() -> int:
    """A port number on this machine that nothing is using right now.

    Asking for port 0 makes the system pick a free one. We read which, and let it go for ssh.
    (Something else could take it in between; that is rare, and ssh then fails clearly.)
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _port_accepts_connections(port: int) -> bool:
    """True if something on this machine answers on `port`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False
