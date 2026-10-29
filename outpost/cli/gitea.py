"""outpost gitea setup|info|backup|restore: install, inspect, back up or restore the Gitea server."""

from pathlib import Path

from outpost import egress, gitea
from outpost.cli.output import say
from outpost.system import AirGappedSystem
from outpost.workspace import STATE_GITEA


def add_parser(commands) -> None:
    parser = commands.add_parser("gitea", help="install, inspect, back up or restore the Gitea server")
    parser.add_argument("action", choices=["setup", "info", "backup", "restore"])
    parser.add_argument("archive", nargs="?", help="restore: the backups/*.tar.gz to restore")
    parser.add_argument("--out", help="backup: folder for the archive (default: backups/ in the workspace)")
    parser.add_argument(
        "--keep-on-server",
        action="store_true",
        help="backup: also leave the archive in /var/tmp/outpost-backups on the server",
    )
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    if args.action == "setup":
        return _setup(system)
    if args.action == "backup":
        gitea.backup(system, Path(args.out) if args.out else None, keep_on_server=args.keep_on_server, log=say)
        return 0
    if args.action == "restore":
        if not args.archive:
            raise RuntimeError("gitea restore needs the archive: outpost gitea restore backups/gitea-....tar.gz")
        gitea.restore(system, Path(args.archive), log=say)
        return 0
    return _info(system)


def _setup(system: AirGappedSystem) -> int:
    installed = gitea.setup(system, log=say)
    say()
    say(f"Gitea {installed['version']} is up at https://{installed['name']} ({installed['ip']})")
    say(f"  web login: {system.servers.gitea.user} / secrets/gitea-admin-password")
    say("Next: outpost upload")
    return 0


def _info(system: AirGappedSystem) -> int:
    state = system.state(STATE_GITEA)
    if not state:
        say("Gitea has not been set up yet. Run: outpost gitea setup")
        return 1
    for key, value in state.items():
        say(f"  {key}: {value}")
    say(f"  egress: {egress.seal_status(system.gitea_host)}")
    return 0
