"""The shell commands outpost.ssh sends, run here with bash against a fake `ip` program."""

import subprocess

import pytest

from outpost.ssh.commands import ADDRESS_ON_DEFAULT_ROUTE


@pytest.mark.parametrize(
    "route",
    [
        "1.0.0.0 via 10.0.0.1 dev eth0 src 10.0.0.5 uid 0",  # through a gateway
        "1.0.0.0 dev eth0 src 10.0.0.5 uid 0",  # no gateway: the network is directly attached
    ],
)
def test_address_on_default_route_is_the_word_after_src(tmp_path, route):
    fake_ip = tmp_path / "ip"
    fake_ip.write_text(f"#!/bin/sh\necho '{route}'\n")
    fake_ip.chmod(0o755)
    printed = subprocess.run(
        ["bash", "-c", ADDRESS_ON_DEFAULT_ROUTE],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert printed.stdout == "10.0.0.5\n"
