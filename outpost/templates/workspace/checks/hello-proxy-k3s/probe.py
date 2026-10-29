"""Run by the hello-proxy checks inside a container: python probe.py <allowed name> <name to refuse>

Prints three lines, one per path, which the check's steps look for:
  allowed-through-proxy=reached    https://<allowed>/ through $HTTPS_PROXY got an HTTP answer
  other-through-proxy=refused-403  the proxy answered CONNECT <other>:443 with 403
  allowed-direct=blocked           without the proxy, the same name is not reachable (the seal)
"""

import sys
import urllib.error
import urllib.request


def fetch(name: str, direct: bool = False) -> str:
    handlers = [urllib.request.ProxyHandler({})] if direct else []  # no handler: $HTTPS_PROXY from the environment
    try:
        urllib.request.build_opener(*handlers).open(f"https://{name}/", timeout=20)
        return "reached"
    except urllib.error.HTTPError:
        return "reached"  # an HTTP status, even 404, came from the far end
    except Exception as e:
        reason = str(getattr(e, "reason", e))
        return "refused-403" if "403" in reason else f"failed ({reason})"


allowed, other = sys.argv[1], sys.argv[2]
print(f"allowed-through-proxy={fetch(allowed)}")
print(f"other-through-proxy={fetch(other)}")
direct = fetch(allowed, direct=True)
print(f"allowed-direct={'LEAK' if direct == 'reached' else 'blocked'} ({direct})")
