"""outpost proxy setup|verify: the egress proxy, the named exceptions to the seal (servers.yml egress_proxy)."""

from outpost.cli.output import say
from outpost.infra.proxy import set_up_egress_proxy, verify_egress_proxy
from outpost.system import AirGappedSystem


def add_parser(commands) -> None:
    parser = commands.add_parser(
        "proxy", help="the egress proxy: named exceptions to the seal (servers.yml egress_proxy)"
    )
    parser.add_argument("action", choices=["setup", "verify"])
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    endpoint = set_up_egress_proxy(system, log=say) if args.action == "setup" else None
    if not verify_egress_proxy(system, log=say):
        return 1
    if endpoint:
        say(
            f"Programs opt in with HTTPS_PROXY={endpoint.url} (see /etc/outpost/proxy.env on every node); "
            "those that do not still have no internet. Next: outpost check hello-proxy --infra <name>"
        )
    return 0
