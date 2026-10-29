"""outpost needs: show what will be fetched."""

import json

from outpost.cli.output import say
from outpost.needs import Needs, all_needs
from outpost.system import AirGappedSystem

SHOWN = 6  # how many pypi specs and npm packages to show before "..."


def add_parser(commands) -> None:
    commands.add_parser("needs", help="show what will be fetched").set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    needs = all_needs(system)
    say(f"Needs from {', '.join(needs.sources) or 'no needs files'}:")
    for line in _lines(needs):
        say(line)
    proxy = system.servers.egress_proxy
    if proxy:
        say(f"  (tinyproxy is for the egress proxy on {proxy.host}, from servers.yml egress_proxy)")
    say(f"Total: {json.dumps(needs.summary())}")
    return 0


def _lines(needs: Needs) -> list[str]:
    lines = [f"  image  {ref}" for ref in needs.images]
    for python, specs in needs.pypi.items():
        lines.append(f"  pypi   (python {python}) {len(specs)} specs: {_first_few(specs)}")
    if needs.npm:
        lines.append(f"  npm    {len(needs.npm)} packages: {_first_few([f'{n.name}@{n.version}' for n in needs.npm])}")
    lines += [f"  apt    ({distro}) {', '.join(packages)}" for distro, packages in needs.apt.items()]
    lines += [f"  apk    ({branch}) {', '.join(packages)}" for branch, packages in needs.apk.items()]
    lines += [f"  rpm    ({distro}) {', '.join(packages)}" for distro, packages in needs.rpm.items()]
    lines += [f"  file   {file.name} {file.version}  <- {file.url}" for file in needs.files]
    return lines


def _first_few(items: list[str]) -> str:
    return ", ".join(items[:SHOWN]) + (" ..." if len(items) > SHOWN else "")
