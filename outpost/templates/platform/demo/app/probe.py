"""Try to reach the public internet, and Gitea, and say what happened.

    python probe.py                      print the results
    python probe.py --write proof.json   also write them as JSON
    python probe.py --fail-if-reachable  exit 1 when any public target answered (used in the Dockerfile)
    python probe.py --proxy              also go through the egress proxy, if this container has one

"rejected" means the connection was refused immediately: the node's firewall said no.
"timeout" means the packets were dropped: the firewall's backstop (used for containers and
pods, where a reject is not possible) or no path out at all.
"unresolvable" means DNS did not even know the name: no internet at all.
All three mean the same thing for the proof: nothing public answered.

These probes are plain TCP connects, so they never use HTTPS_PROXY: they test the direct path, which
must stay closed with or without an egress proxy (a `docker build` on a node with a proxy gets the proxy
variables as build arguments; the build-time proof still measures the direct path).

The egress proxy is measured separately (probe_proxy): an HTTPS request through $HTTPS_PROXY to every name
in $OUTPOST_PROXY_ALLOWED (from /etc/outpost/proxy.env or the outpost-proxy ConfigMap), which must be
"reached", and to example.com, which the proxy must refuse with 403. Without the variables: proxy_ok is None.
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REFUSED_PROBE = "example.com"  # never on an allow list, unless you put it there
PUBLIC = [
    ("pypi.org", 443),
    ("registry.npmjs.org", 443),
    ("deb.debian.org", 80),
    ("registry-1.docker.io", 443),
    ("1.1.1.1", 443),
]


def probe(host: str, port: int, timeout: float = 2.0) -> dict:
    """One TCP connect to the first IPv4 address of `host`, so every probe takes at most `timeout`."""
    started = time.monotonic()
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    except socket.gaierror as e:
        return {
            "target": f"{host}:{port}",
            "result": "unresolvable",
            "detail": str(e),
            "ms": int((time.monotonic() - started) * 1000),
        }
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        result, detail = "REACHED", f"{addr[0]} accepted the connection"
    except socket.timeout:
        result, detail = (
            "timeout",
            f"{addr[0]} did not answer within {timeout}s (packets dropped by the firewall, or no route)",
        )
    except OSError as e:
        result, detail = "rejected", f"{addr[0]}: {getattr(e, 'strerror', None) or e}"
    finally:
        sock.close()
    return {
        "target": f"{host}:{port}",
        "result": result,
        "detail": detail,
        "ms": int((time.monotonic() - started) * 1000),
    }


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


def through_proxy(name: str, proxy: str, timeout: float = 3.0) -> dict:
    """One HTTPS request to https://<name>/ through the proxy. Any HTTP answer, even 404, means the far end
    was reached through the tunnel; a 403 to CONNECT means the proxy refused the name."""
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    try:
        with opener.open(f"https://{name}/", timeout=timeout) as resp:
            result, detail = "reached", f"HTTP {resp.status} through the proxy"
    except urllib.error.HTTPError as e:
        result, detail = "reached", f"HTTP {e.code} through the proxy"
    except Exception as e:  # URLError, timeouts, TLS errors
        reason = str(getattr(e, "reason", e))
        result = "refused" if "403" in reason else "failed"
        detail = "the proxy refused the name (403)" if result == "refused" else reason
    return {"target": name, "result": result, "detail": detail, "ms": int((time.monotonic() - started) * 1000)}


def probe_proxy() -> dict | None:
    """The egress proxy, as this container sees it; None when it has no proxy variables."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    names = [n.strip().removeprefix("*.") for n in os.environ.get("OUTPOST_PROXY_ALLOWED", "").split(",") if n.strip()]
    if not proxy or not names:
        return None
    targets = list(dict.fromkeys(names)) + ([REFUSED_PROBE] if REFUSED_PROBE not in names else [])
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda n: through_proxy(n, proxy), targets))
    for r in results:
        r["expected"] = "refused" if r["target"] == REFUSED_PROBE and REFUSED_PROBE not in names else "reached"
    return {"url": proxy, "results": results, "ok": all(r["result"] == r["expected"] for r in results)}


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
    if "--proxy" in sys.argv:
        via = probe_proxy()
        for r in (via or {}).get("results", []):
            print(f"  via proxy {r['target']:22} {r['result']:9} (expected {r['expected']}) {r['detail']}")
        print(
            "no egress proxy variables in this container"
            if via is None
            else f"proxy {'as expected' if via['ok'] else 'NOT as expected'}"
        )
