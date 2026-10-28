"""The parser, built from COMMANDS, and main(): run one command and turn its errors into exit codes.

exit 0    the command did what it said
exit 1    it failed; stderr has `error: <what to do>` (or the output says what failed)
exit 2    the command line was wrong
exit 130  Ctrl-C
"""

import argparse
import sys

from outpost import __version__
from outpost import cli as cli_package
from outpost.cli import (
    check,
    demo,
    egress,
    fetch,
    gitea,
    hosts,
    infra,
    init,
    lab,
    needs,
    pack,
    pki,
    proxy,
    report,
    ui,
    upload,
)
from outpost.cli.output import say_error
from outpost.config import ConfigError
from outpost.http import HttpError
from outpost.ssh import SshError

COMMANDS = (init, needs, fetch, pack, gitea, upload, infra, check, demo, egress, proxy, ui, lab, hosts, report, pki)
EXPECTED_ERRORS = (ConfigError, RuntimeError, SshError, HttpError, ValueError)  # printed as one line, exit 1


def main(argv: list[str] | None = None) -> int:
    """Run one outpost command. Returns its exit code."""
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except EXPECTED_ERRORS as e:
        say_error(str(e))
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    """The parser for every command; each one sets `args.run` to its run function."""
    parser = argparse.ArgumentParser(
        prog="outpost", description=cli_package.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", default=".", help="workspace (default: current directory)")
    parser.add_argument("--version", action="version", version=f"outpost {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        command.add_parser(commands)
    return parser
