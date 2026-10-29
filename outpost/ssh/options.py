"""The ssh config file to use, and the options every ssh and scp call gets.

    config_file = choose_config_file(servers.ssh_config)   # once, after servers.yml is read
    host = Host("gitea-1", config_file=config_file)        # every Host and Tunnel gets it passed in

The chosen file is never kept in a global: AirGappedSystem holds it and gives it to each Host it
makes, and ssh_options() adds it to that Host's ssh and scp calls.
"""

import os
from pathlib import Path

from outpost.ssh.errors import SshError

CONFIG_FILE_ENV = "OUTPOST_SSH_CONFIG"  # names the ssh config file when servers.yml does not


def choose_config_file(path: str | Path | None) -> Path | None:
    """The ssh config file every ssh and scp call should use, or None for ssh's default, ~/.ssh/config.

    The choice, in order:
        1. `path`, from `ssh_config:` in servers.yml
        2. the OUTPOST_SSH_CONFIG environment variable
        3. neither: ssh uses your normal ~/.ssh/config

    Raises:
        SshError: if 1 and 2 are both set to different files, or the chosen file does not exist.
            We check now, because ssh would only fail later, with exit 255, which looks like a
            server that is down.
    """
    from_env = os.environ.get(CONFIG_FILE_ENV)
    if path and from_env and Path(path).resolve() != Path(from_env).resolve():
        raise SshError(
            f"two ssh config files are named: {path} (ssh_config: in servers.yml) and "
            f"{from_env} ({CONFIG_FILE_ENV}). Keep one, or make them the same."
        )
    chosen, named_by = (path, "ssh_config: in servers.yml") if path else (from_env, CONFIG_FILE_ENV)
    if chosen and not Path(chosen).is_file():
        raise SshError(f"ssh config file {chosen} does not exist (named by {named_by})")
    return Path(chosen) if chosen else None


def ssh_options(connect_timeout: int, config_file: Path | None) -> list[str]:
    """The options every ssh and scp call gets.

    Args:
        connect_timeout: seconds to wait for the server to answer before giving up.
        config_file: the ssh config file to use; None means ssh's default, ~/.ssh/config.
    """
    options = [
        "-o",
        "BatchMode=yes",  # never ask for a password or anything else; fail instead
        "-o",
        f"ConnectTimeout={connect_timeout}",  # give up if the server does not answer in time
        "-o",
        "LogLevel=ERROR",  # hide ssh's warnings, so they don't mix into the command's output
    ]
    if config_file:
        options += ["-F", str(config_file)]  # -F: use this config file instead of ~/.ssh/config
    return options
