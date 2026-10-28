"""The demo: a real project, pushed to Gitea, built and deployed by Gitea Actions on sealed nodes.

  outpost demo push      copy demo/ into the workspace if missing, write its workflow from servers.yml,
                         commit everything into the <org>/demo repository; Actions takes it from there
  outpost demo status    the latest workflow runs, and what each infrastructure is serving

    demo.push(system, log=print)        # {"commit", "repo", "actions_url", "infras"}
    demo.status(system)                 # {"repo", "actions_url", "runs", "serving"}
    demo.demo_urls(system)              # per infrastructure: node, port, URL

The workflow has one `build` job (runs on a node with Docker, pushes the image into Gitea's registry)
and one deploy job per infrastructure in servers.yml. Jobs run on the Outpost runners, which are the
nodes themselves, so nothing in the pipeline can reach anything but Gitea. The page the site serves
measures that, at build time and on every request.

    project.py      ensure_local(), project_files(), git_blob_sha(), NEEDS_YML: demo/ on the laptop
    workflow.py     workflow(), PORTS, and the step templates
    push.py         push(), package_token()
    status.py       status(), demo_urls()

Code outside this folder uses only the names below.
"""

from outpost.demo.project import NAME, NEEDS_YML, ensure_local, git_blob_sha, project_files
from outpost.demo.push import package_token, push
from outpost.demo.status import demo_urls, status
from outpost.demo.workflow import PORTS, workflow

__all__ = [
    "NAME",
    "NEEDS_YML",
    "PORTS",
    "demo_urls",
    "ensure_local",
    "git_blob_sha",
    "package_token",
    "project_files",
    "push",
    "status",
    "workflow",
]
