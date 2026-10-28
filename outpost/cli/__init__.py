"""The command line. One function per command, in the order you would use them.

  outpost init            make a workspace to fill in
  outpost needs           show what will be fetched
  outpost fetch           download everything into box/        (needs internet)
  outpost pack            turn box/ into signed tar.xz files   (for GitHub releases or USB)
  outpost gitea setup     install Gitea on its server          (needs SSH)
  outpost upload          push the box into Gitea              (needs SSH)
  outpost infra setup X   configure the nodes of infrastructure X
  outpost infra verify X  prove the nodes use Gitea and nothing else
  outpost check           run the smoke checks on an infrastructure
  outpost demo push       push the demo project to Gitea; Actions builds and deploys it on the sealed nodes
  outpost egress          seal or open a server's outbound internet
  outpost proxy setup     the egress proxy: named exceptions to the seal (api.twilio.com...), if servers.yml has one
  outpost proxy verify    prove it lets the allowed names through and refuses the rest
  outpost ui              the same, as a local web page
  outpost lab up|down     throwaway servers in Docker, to see a full run without any VPS

Day two:
  outpost hosts           what every server runs right now (versions, seal, workloads)
  outpost gitea backup    archive Gitea's data to backups/; `gitea restore <file>` puts it back
  outpost infra upgrade X OS upgrade on the nodes (apt, apk or dnf), from Gitea
  outpost pki rotate      new CA and certificate, pushed everywhere
  outpost report          one HTML page with the evidence, for an auditor or a boss
"""

# This docstring is also `outpost --help`. The code, for the reader:
#
#   main.py      COMMANDS (the modules below, in the order above), build_parser(), main(): errors -> exit codes
#   output.py    say(): the only place that prints
#   init.py needs.py fetch.py pack.py gitea.py upload.py infra.py check.py demo.py
#   egress.py proxy.py ui.py lab.py hosts.py report.py pki.py
#                one command each: add_parser(commands) declares its flags, run(args) -> exit code is
#                a short recipe: load the AirGappedSystem, call one or two functions, print the outcome.

from outpost.cli.main import build_parser, main

__all__ = ["build_parser", "main"]
