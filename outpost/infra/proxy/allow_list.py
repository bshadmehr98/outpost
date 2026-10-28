"""The egress proxy's allow list: servers.yml's egress_proxy.allow, as tinyproxy's filter file.

allow_regex("api.twilio.com")      # ^api\\.twilio\\.com$
allow_regex("*.sendgrid.net")      # (^|\\.)sendgrid\\.net$: the domain and every name under it
probe_name(allow), refused_name(allow)   # names to prove the proxy lets through, and refuses
"""

import re

REFUSAL_PROBES = ("example.com", "example.net", "example.org")  # documentation domains, IANA reserved


def allow_regex(entry: str) -> str:
    """The anchored extended regex for one allow-list entry. Names only hold [a-z0-9.-], so escaping
    the dots is all the escaping needed."""
    if entry.startswith("*."):
        return "(^|\\.)" + entry[2:].replace(".", "\\.") + "$"
    return "^" + entry.replace(".", "\\.") + "$"


def allow_file(allow: list[str], gitea_name: str) -> str:
    """The text of the filter file: one regex per line, the Gitea name first."""
    lines = [
        "# Written by `outpost proxy setup` from servers.yml egress_proxy.allow. Edit there, not here.",
        "# One extended regular expression per line, matched case-insensitively against the host name a",
        "# client asks for (CONNECT host:443), never the URL. Everything that matches no line is refused.",
        "# The Gitea name is always allowed, so a client that ignores NO_PROXY still reaches Gitea, which",
        "# the seal lets every host reach anyway.",
        allow_regex(gitea_name),
        *[allow_regex(a) for a in allow if a != gitea_name],
    ]
    return "\n".join(lines) + "\n"


def allowed_by(allow: list[str], name: str) -> bool:
    """True when the allow list lets name through."""
    return any(re.search(allow_regex(a), name) for a in allow)


def probe_name(allow: list[str]) -> str:
    """A name to test the proxy with: the first plain name, else the domain of the first wildcard."""
    plain = [a for a in allow if not a.startswith("*.")]
    return plain[0] if plain else allow[0][2:]


def refused_name(allow: list[str]) -> str:
    """A name the proxy must refuse: a documentation domain the allow list does not cover."""
    return next((n for n in REFUSAL_PROBES if not allowed_by(allow, n)), "refused.invalid")
