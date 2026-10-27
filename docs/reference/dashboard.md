# The dashboard

```
outpost ui
```

Opens `http://localhost:8484` for the platform folder you are in. Everything on the page is read
from the files in that folder, and every button starts `outpost <command>` as a subprocess, so the
page can never do anything the command line cannot. The command behind each button is printed
next to it, with a copy icon, so the page doubles as a tutorial for the CLI.

![Overview page](../images/dashboard-overview.png)

## Pages

The dashboard is written for someone who has never used the command line and does not want to.
Pages are named for what they are about, not for the command behind them; the command is shown
small, next to the title and next to every button.

**Home.** Three questions, each answered in one line with a colour and a reason: *Is everything
downloaded? Does the local server have it? Are the servers sealed and working?* Below them the
flow (projects → box → local internet → servers) with every arrow in sync or behind, a "Next"
card, what needs attention (a needs file that does not parse, a missing tool, failed downloads,
a machine not at the pinned versions, no backup yet), recent activity in plain words, and a check
of this computer. On an empty platform folder the Home page is a **guided list** instead: the
eight steps in order, each with what it is and how long it takes, and a "Start" button on the
next one.

**Machines.** A diagram (this computer → Gitea → the servers) and one card per machine from the
last look over SSH: internet switched off or OPEN, system, address, what Outpost installed, disk,
uptime. Click a card for the detail: every version against the pin, the services, what runs there
(Compose projects, Swarm stacks and services, Kubernetes deployments, Gitea repositories and
package size), the internet-switch log, and buttons to look again, switch the internet on or
off with a reason, or run that machine's setup again. The egress proxy's host shows the role
*egress proxy*, tinyproxy with its service, and the last lines of the proxy log. *Look again at every machine* refreshes
all of them; a checkbox makes the page do it every five minutes.

**Applications.** Per infrastructure: set up or not, what is running there, the URL the demo
opens at, and the demo's own verdict (built with no internet, running with no internet). The
latest deployments from Gitea Actions, and how to bring your own application.

**Steps 1 to 8.** One page per step, each with a short explanation, a "How it works" fold, a
status card, and a **What this does** box before the main button: what will happen, which
machines it touches, how long it usually takes, whether it needs internet.

| Step | Page | What it does |
|---|---|---|
| 1 | Your servers | fill in SSH aliases and infrastructures, or create a practice lab in Docker; test each connection; the optional *Egress proxy* card: allowed names, proxy host (empty for the Gitea host), port, distro |
| 2 | What your projects need | scan a folder, upload a lockfile or write the list; *Scan again* shows what is new; *Do we have…?* searches |
| 3 | Download the supplies | box size by kind, what the next download gets, what failed and why, what is in the box but no longer needed |
| 4 | Your local internet | where Gitea goes, Set up, its address, token and web links |
| 5 | Fill the local internet | push the box; how many files are new since the last push |
| 6 | Set up the servers | Set up and Verify per infrastructure, the verification table, per-node seal status and log |
| 7 | Deploy an application | push the demo; the workflow runs and what each node serves |
| 8 | Prove it | smoke checks with their output; the internet switch with a reason |

**While a command runs** the page shows its progress as a list of steps (*2/7 certificates*,
*lab-node-1: switching the internet off*, …) with the full output folded underneath. When a
command fails, the first known error is translated into a sentence and a fix ("The server did
not answer on SSH. Is it on, and is port 22 open from this computer?"); unknown errors show the
last lines of the output.

**Keep it healthy.** Backups of Gitea (last one, the archives in `backups/`, back up now,
restore one), the versions table (pinned against every machine), security updates from Gitea,
rotating the CA, the named exceptions (the allowed names, the proxy's address, when it was set up
and verified, with *Set up the proxy* and *Verify the proxy*), and the proof report.

**History.** Every command started from the page, newest first, as a sentence ("Uploaded 50 new
files to Gitea", "Set up and verified dev: 1 node all good", "failed: A disk is full."), with
the duration; click one for its steps and full output.

**Proof report.** `/report.html`: one printable page with the machines, their seal state and
versions, every verification table and smoke check with its date, and what the deployed demo
reports. The same page as `outpost report`.

## Scanning a project

Point step 2 at a folder on your computer. Outpost looks three levels deep, skipping
`node_modules`, virtualenvs and the like, for:

- lockfiles: `requirements*.txt`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`
- Dockerfiles: their `FROM` images (build stages and the config image are skipped; a Gitea name is read back into the need that produces it: `${GITEA}/${ORG}/library--python:3.12` becomes `python:3.12`, `gitea.internal/pkgs/bitnami--redis:7` becomes `bitnami/redis:7`) and `apt-get install`, `apk add` and `dnf install` packages, each proposed under its own kind with the distro or branch read from the base image
- the Python version, from `.python-version`, `pyproject.toml` or the `python:` base image

It proposes a needs file; you tick the lockfiles, fix the lists, and save. Lockfile paths are
stored relative to `needs/`, and the folder is remembered so *Scan again* can show new images or
apt packages after a Dockerfile change. Lockfile changes need no rescan: the next fetch reads them.

## Safety

The server binds to 127.0.0.1 only. It edits only `servers.yml`, `mirrors.yml` and YAML files under
`needs/` and `checks/`, writes uploads under `needs/<project>/`, runs only the commands on its allow
list, one at a time, and reads servers over SSH only when you click (connection test, seal status,
*Look again*) or when you turned the five-minute heartbeat on. Anything that changes a machine
goes through the CLI and lands in `state/runs/` like everything else.
