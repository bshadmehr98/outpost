"""Talking to servers. Outpost never installs anything on a server except through these calls.

  Host.run(script)       run a bash script as root over SSH, using your ~/.ssh/config aliases
  Host.write(path, text) put a text file in place (mode 0644 unless told otherwise)
  Host.put(local, remote) copy a binary file with scp
  Host.get(remote, local) copy a file back with scp (the remote file must be readable by the SSH user)
  Tunnel(alias, port)    forward a local port to a port on the server (how we reach Gitea's API)

The remote user must be root or have passwordless sudo. Tests replace Host with a fake.
`ssh_config:` in servers.yml (or the OUTPOST_SSH_CONFIG environment variable) selects an alternative
ssh config file; `outpost lab up` uses that so the lab never touches your ~/.ssh/config.
"""

import os
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Become root if we are not already. `sudo -n` fails loudly instead of waiting for a password.
_AS_ROOT = 'if [ "$(id -u)" = 0 ]; then bash -s; else sudo -n bash -s; fi'


class SshError(Exception):
    pass


_config_file: str | None = None


def use_config(path: str | Path | None) -> None:
    """Make every Host and Tunnel use this ssh config file instead of ~/.ssh/config."""
    global _config_file
    _config_file = str(path) if path else None


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _base_opts(connect_timeout: int) -> list[str]:
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}", "-o", "LogLevel=ERROR"]
    config = _config_file or os.environ.get("OUTPOST_SSH_CONFIG")
    if config:
        opts += ["-F", config]
    return opts


class Host:
    def __init__(self, alias: str, connect_timeout: int = 20):
        self.alias = alias
        self.ssh_opts = _base_opts(connect_timeout)

    def __repr__(self) -> str:
        return f"Host({self.alias})"

    def run(self, script: str, check: bool = True, timeout: int = 1800) -> Result:
        proc = subprocess.run(["ssh", *self.ssh_opts, self.alias, _AS_ROOT], input=("set -euo pipefail\n" + script).encode(),
                              capture_output=True, timeout=timeout)
        result = Result(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))
        if check and not result.ok:
            raise SshError(f"[{self.alias}] command failed (exit {result.returncode}):\n{result.stderr.strip() or result.stdout.strip()}")
        return result

    def write(self, path: str, content: str | bytes, mode: str = "0644") -> None:
        data = content.encode() if isinstance(content, str) else content
        script = f"mkdir -p {shlex.quote(str(Path(path).parent))} && cat > {shlex.quote(path)} && chmod {mode} {shlex.quote(path)}"
        proc = subprocess.run(["ssh", *self.ssh_opts, self.alias, f"{_AS_ROOT.replace('bash -s', 'bash -c ' + shlex.quote(script))}"],
                              input=data, capture_output=True)
        if proc.returncode != 0:
            raise SshError(f"[{self.alias}] could not write {path}: {proc.stderr.decode().strip()}")

    def put(self, local: Path, remote: str, mode: str = "0644", via: str = "/tmp") -> None:
        """scp to a staging folder the SSH user can write, then move into place as root.
        `via` matters for big files: /tmp is often a small tmpfs, /var/tmp is on disk."""
        tmp = f"{via}/outpost-{local.name}"
        proc = subprocess.run(["scp", *self.ssh_opts, "-q", str(local), f"{self.alias}:{tmp}"], capture_output=True)
        if proc.returncode != 0:
            raise SshError(f"[{self.alias}] scp {local.name} failed: {proc.stderr.decode().strip()}")
        self.run(f"mkdir -p {shlex.quote(str(Path(remote).parent))} && mv {shlex.quote(tmp)} {shlex.quote(remote)} && chmod {mode} {shlex.quote(remote)}")

    def get(self, remote: str, local: Path) -> Path:
        local.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["scp", *self.ssh_opts, "-q", f"{self.alias}:{remote}", str(local)], capture_output=True)
        if proc.returncode != 0:
            raise SshError(f"[{self.alias}] scp {remote} failed: {proc.stderr.decode().strip()}")
        return local

    def ip(self) -> str:
        """The address other machines should use to reach this host."""
        out = self.run("hostname -I 2>/dev/null | awk '{print $1}' || ip -4 route get 1 | awk '{print $7; exit}'").stdout.strip()
        if not out:
            raise SshError(f"[{self.alias}] could not detect an IP address; set it in servers.yml")
        return out.split()[0]

    def facts(self) -> dict:
        """OS id, family (ID_LIKE), version, codename, architecture and the first IP, in one round trip."""
        out = self.run(". /etc/os-release; echo \"id=$ID\"; echo \"id_like=${ID_LIKE:-}\"; echo \"version_id=${VERSION_ID:-}\"; "
                       "echo \"codename=${VERSION_CODENAME:-}\"; echo \"arch=$(uname -m)\"; "
                       "echo \"ip=$(hostname -I 2>/dev/null | awk '{print $1}')\"; echo \"hostname=$(hostname)\"").stdout
        return dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)


class Tunnel:
    """`with Tunnel('gitea-1', 443) as t:` gives t.port on 127.0.0.1 forwarded to the server's port 443."""

    def __init__(self, alias: str, remote_port: int = 443, connect_timeout: int = 20):
        self.alias = alias
        self.remote_port = remote_port
        self.connect_timeout = connect_timeout
        self.port: int | None = None
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "Tunnel":
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self._proc = subprocess.Popen(
            ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", *_base_opts(self.connect_timeout),
             "-L", f"127.0.0.1:{self.port}:127.0.0.1:{self.remote_port}", self.alias],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + self.connect_timeout + 5
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise SshError(f"tunnel to {self.alias} failed: {self._proc.stderr.read().decode().strip()}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return self
            except OSError:
                time.sleep(0.2)
        self.__exit__(None, None, None)
        raise SshError(f"tunnel to {self.alias} did not come up in time")

    def __exit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
