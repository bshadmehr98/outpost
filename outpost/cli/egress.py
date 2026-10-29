"""outpost egress <target> seal|open|status: block or allow a server's outbound internet."""

from outpost import egress
from outpost.cli.output import say
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser("egress", help="seal or open outbound internet on a server")
    parser.add_argument("target", help="gitea, an infrastructure name, or an SSH alias")
    parser.add_argument("action", choices=["seal", "open", "status"])
    parser.add_argument("--reason", help="written to the server's egress log")
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    reason = args.reason or ""
    for host in egress.target_hosts(system, args.target):
        if args.action == "status":
            say(f"  {host.alias}: {egress.seal_status(host)}")
        elif args.action == "seal":
            say(f"  {host.alias}: {egress.seal_host(host, reason)}")
        else:
            say(f"  {host.alias}: {egress.open_host(host, reason)}  (remember: outpost egress {args.target} seal)")
    return 0
