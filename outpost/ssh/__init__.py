"""Run commands and copy files on a server, over ssh and scp.

    host = Host("gitea-1")                                  # a name from your ssh config
    host = Host("gitea-1", config_file=Path("lab/ssh_config"))   # ... from another ssh config file
    host.run("apt-get update")                              # run a bash script as root
    host.write("/etc/motd", "hello\\n")                      # create a file from a string
    host.put(Path("box/gitea"), "/usr/local/bin/gitea")     # upload a file
    host.get("/var/tmp/backup.tar.gz", Path("backup.tgz"))  # download a file
    host.ip(), host.facts()                                 # the server's address, OS and CPU type

    with Tunnel("gitea-1", 443) as tunnel:                  # 127.0.0.1:{tunnel.port} here -> port 443 there
        ...

The server needs only sshd, and a login that is root or may use sudo without a password.
Every ssh and scp call is logged at DEBUG level on the `outpost.ssh` logger.
Tests use FakeHost (tests/conftest.py) instead of Host.

    errors.py       Result, and the errors: SshError, Unreachable, CommandFailed
    host.py         Host
    tunnel.py       Tunnel
    options.py      choose_config_file(), and the options every ssh and scp call gets
    markers.py      telling "ssh failed" from "the command failed"
    commands.py     the shell commands Host sends
    permissions.py  a file's owner and permissions, in words, for the logs

Code outside this folder uses only the names below.
"""

from outpost.ssh.errors import CommandFailed, Result, SshError, Unreachable
from outpost.ssh.host import Host
from outpost.ssh.options import choose_config_file
from outpost.ssh.tunnel import Tunnel

__all__ = ["CommandFailed", "Host", "Result", "SshError", "Tunnel", "Unreachable", "choose_config_file"]
