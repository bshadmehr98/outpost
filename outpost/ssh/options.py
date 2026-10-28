"""The ssh config file to use, and the options every ssh and scp call gets.

use_config() is called once, after servers.yml is read. After that, ssh_options() adds the chosen
file to every ssh and scp call, so they all use the same one.
"""

import os
from pathlib import Path

from outpost.ssh.errors import SshError

_config_file: str | None = None  # the chosen ssh config file; None means ssh's default, ~/.ssh/config


def use_config(path: str | Path | None) -> None:
    """Choose the ssh config file for every later ssh and scp call.

    The choice, in order:
        1. `path`, from `ssh_config:` in servers.yml
        2. the OUTPOST_SSH_CONFIG environment variable
        3. neither: ssh uses your normal ~/.ssh/config

    Raises:
        SshError: if 1 and 2 are both set to different files, or the chosen file does not exist.
            We check now, because ssh would only fail later, with exit 255, which looks like a
            server that is down.
    """
    global _config_file
    from_env = os.environ.get("OUTPOST_SSH_CONFIG")
    if path and from_env and Path(path).resolve() != Path(from_env).resolve():
        raise SshError(
            f"two ssh config files are named: {path} (ssh_config: in servers.yml) and "
            f"{from_env} (OUTPOST_SSH_CONFIG). Keep one, or make them the same."
        )
    chosen, named_by = (path, "ssh_config: in servers.yml") if path else (from_env, "OUTPOST_SSH_CONFIG")
    if chosen and not Path(chosen).is_file():
        raise SshError(f"ssh config file {chosen} does not exist (named by {named_by})")
    _config_file = str(chosen) if chosen else None


def ssh_options(connect_timeout: int) -> list[str]:
    """The options every ssh and scp call gets.

    Args:
        connect_timeout: seconds to wait for the server to answer before giving up.
    """
    options = [
        "-o",
        "BatchMode=yes",  # never ask for a password or anything else; fail instead
        "-o",
        f"ConnectTimeout={connect_timeout}",  # give up if the server does not answer in time
        "-o",
        "LogLevel=ERROR",  # hide ssh's warnings, so they don't mix into the command's output
    ]
    if _config_file:
        options += ["-F", _config_file]  # -F: use this config file instead of ~/.ssh/config
    return options
