"""outpost infra setup|verify|upgrade|list: the nodes of one infrastructure."""

from outpost.cli.output import say, say_error
from outpost.infra.setup import set_up_infra, verify_infra
from outpost.infra.upgrade import upgrade_nodes
from outpost.system import AirGappedSystem
from outpost.workspace import verify_state


def add_parser(commands) -> None:
    parser = commands.add_parser("infra", help="set up, verify or upgrade an infrastructure")
    parser.add_argument("action", choices=["setup", "verify", "upgrade", "list"])
    parser.add_argument("name", nargs="?")
    parser.set_defaults(run=run)


def run(args) -> int:
    if args.action != "list" and not args.name:
        say_error("infra setup/verify needs an infrastructure name")
        return 2
    system = AirGappedSystem.load(args.dir)
    if args.action == "list":
        return _list(system)
    infra = system.infra(args.name)
    if args.action == "upgrade":
        return 0 if upgrade_nodes(system, system.nodes(infra.name), log=say) else 1
    if args.action == "verify":
        return 0 if verify_infra(system, infra, log=say) else 1
    if not set_up_infra(system, infra, log=say):
        return 1
    say(
        f"Next: outpost check --infra {infra.name}   (smoke check)   or   outpost demo push   (deploy the demo through Gitea Actions)"
    )
    return 0


def _list(system: AirGappedSystem) -> int:
    for name, infra in system.servers.infrastructures.items():
        last_verify = (system.state(verify_state(name)) or {}).get("result", "never")
        say(f"  {name:12} {infra.type:8} nodes: {', '.join(infra.nodes)}   last verify: {last_verify}")
    return 0
