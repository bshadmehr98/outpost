"""outpost demo push|status: the example project, built and deployed by Gitea Actions on the sealed nodes."""

from outpost import demo
from outpost.cli.output import say
from outpost.system import AirGappedSystem

NO_RUNS = {"sha": "-", "status": "no runs yet", "conclusion": "", "jobs": [], "when": ""}


def add_parser(commands) -> None:
    parser = commands.add_parser(
        "demo", help="push the demo project; Gitea Actions builds and deploys it on the sealed nodes"
    )
    parser.add_argument("action", choices=["push", "status"])
    parser.set_defaults(run=run)


def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    if args.action == "push":
        demo.push(system.workspace, system.servers, log=say)
        return 0
    status = demo.status(system.workspace, system.servers)
    _say_workflow_runs(status)
    _say_what_is_served(status)
    return 0


def _say_workflow_runs(status: dict) -> None:
    say(f"Workflow runs of {status['repo']}  ({status['actions_url']})")
    for workflow_run in status["runs"] or [NO_RUNS]:
        say(
            f"  {workflow_run['sha']:10} {workflow_run['status']:12} {workflow_run['conclusion'] or '':10} {workflow_run['when'] or ''}"
        )
        for job in workflow_run["jobs"]:
            say(f"      {job['name']:32} {job['status']:12} {job['conclusion'] or ''}")


def _say_what_is_served(status: dict) -> None:
    say("Serving:")
    for name, served in status["serving"].items():
        health = served["health"]
        if not health:
            say(f"  {name:10} nothing on {served['node']}:{served['port']} yet")
            continue
        sealed = (
            "sealed at build and now" if health.get("sealed_at_build") and health.get("sealed_now") else "NOT SEALED"
        )
        say(f"  {name:10} {served['url']}  commit {health.get('sha', '')[:10]}  {sealed}")
