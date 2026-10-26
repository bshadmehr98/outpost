"""Try to reach the public internet, and Gitea, and say what happened.

    python probe.py                      print the results
    python probe.py --write proof.json   also write them as JSON
    python probe.py --fail-if-reachable  exit 1 when any public target answered (used in the Dockerfile)

"rejected" means the connection was refused immediately: the node's firewall said no.
"timeout" means the packets were dropped: the firewall's backstop (used for containers and
pods, where a reject is not possible) or no path out at all.
"unresolvable" means DNS did not even know the name: no internet at all.
All three mean the same thing for the proof: nothing public answered.
"""

import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

PUBLIC = [("pypi.org", 443), ("registry.npmjs.org", 443), ("deb.debian.org", 80), ("registry-1.docker.io", 443), ("1.1.1.1", 443)]


def probe(host: str, port: int, timeout: float = 2.0) -> dict:
    """One TCP connect to the first IPv4 address of `host`, so every probe takes at most `timeout`."""
    started = time.monotonic()
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    except socket.gaierror as e:
        return {"target": f"{host}:{port}", "result": "unresolvable", "detail": str(e), "ms": int((time.monotonic() - started) * 1000)}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        result, detail = "REACHED", f"{addr[0]} accepted the connection"
    except socket.timeout:
        result, detail = "timeout", f"{addr[0]} did not answer within {timeout}s (packets dropped by the firewall, or no route)"
    except OSError as e:
        result, detail = "rejected", f"{addr[0]}: {getattr(e, 'strerror', None) or e}"
    finally:
        sock.close()
    return {"target": f"{host}:{port}", "result": result, "detail": detail, "ms": int((time.monotonic() - started) * 1000)}


def probe_all(gitea: str | None = None) -> dict:
    targets = list(PUBLIC)
    if gitea:
        targets.append((gitea, 443))
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda t: probe(*t), targets))
    public = [r for r in results if gitea is None or r["target"] != f"{gitea}:443"]
    return {
        "when": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "hostname": socket.gethostname(),
        "results": results,
        "sealed": all(r["result"] != "REACHED" for r in public),
        "gitea_ok": any(r["target"] == f"{gitea}:443" and r["result"] == "REACHED" for r in results) if gitea else None,
    }


if __name__ == "__main__":
    report = probe_all(os.environ.get("GITEA"))
    for r in report["results"]:
        print(f"  {r['target']:28} {r['result']:13} {r['detail']} ({r['ms']} ms)")
    if "--write" in sys.argv:
        with open(sys.argv[sys.argv.index("--write") + 1], "w") as f:
            json.dump(report, f, indent=2)
    if "--fail-if-reachable" in sys.argv and not report["sealed"]:
        print("The public internet is reachable from this build. Refusing to build an image that may depend on it.")
        sys.exit(1)
    print("sealed: the public internet is not reachable" if report["sealed"] else "NOT sealed")
