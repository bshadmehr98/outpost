"""The `egress_proxy:` section of servers.yml: one tinyproxy that sealed workloads may use to reach a
short list of allowed names (see docs/architecture/proxy.md)."""

import re
from dataclasses import dataclass, field

from outpost.config.errors import ConfigError

# a DNS label: letters, digits, hyphens inside; the last label (the TLD) must not be all digits, which
# is what keeps an IPv4 address out
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOSTNAME = re.compile(rf"(?:{_LABEL}\.)+[a-z](?:[a-z0-9-]{{0,61}}[a-z0-9])?")

KNOWN_KEYS = {"host", "port", "allow", "distro", "name"}
DEFAULT_PORT = 3128
DEFAULT_NAME = "proxy.internal"


@dataclass
class ProxyConfig:
    """The optional egress proxy: one tinyproxy, sealed like everything else, that may reach only the
    names in `allow` (see docs/architecture/proxy.md)."""

    host: str  # ssh alias of the proxy host; servers.yml default: the Gitea server
    port: int = DEFAULT_PORT
    allow: list[str] = field(default_factory=list)  # host names, or "*.domain" for a domain and its subdomains
    distro: str | None = None  # apt codename or rpm distro of the proxy host, for `outpost needs` / `fetch`
    name: str = DEFAULT_NAME  # the name nodes use when the proxy is not on the Gitea server


def load_egress_proxy(data, gitea_host: str) -> ProxyConfig | None:
    """The egress_proxy section, checked; None when servers.yml has none.

    Args:
        data: the section as YAML gave it (None when it is missing).
        gitea_host: the Gitea server's alias, the proxy host when the section names none.

    Raises:
        ConfigError: a key, the allow list, the port or the name is wrong.
    """
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ConfigError("egress_proxy must be a mapping with `allow:` (and optionally host, port, distro, name)")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise ConfigError(f"egress_proxy has unknown keys: {', '.join(sorted(unknown))}")
    return ProxyConfig(
        host=str(data.get("host") or gitea_host),
        allow=list(dict.fromkeys(proxy_allow_entry(entry) for entry in _allow_list(data.get("allow")))),
        port=_port(data.get("port", DEFAULT_PORT)),
        distro=str(data["distro"]).strip().lower() if data.get("distro") else None,
        name=_proxy_name(data.get("name")),
    )


def proxy_allow_entry(value) -> str:
    """One egress_proxy.allow entry, lower-cased: `api.twilio.com` or `*.sendgrid.net`. No IPs (the
    firewall handles addresses), no schemes, ports or paths (the proxy matches the host name only)."""
    text = str(value).strip().lower()
    if "://" in text or "/" in text or ":" in text:
        raise ConfigError(
            f"egress_proxy.allow: {value!r} must be a host name such as api.twilio.com, without scheme, port or path"
        )
    bare = text[2:] if text.startswith("*.") else text
    if text.startswith("*.") and "." not in bare and re.fullmatch(r"[a-z0-9-]+", bare):
        raise ConfigError(
            f"egress_proxy.allow: {value!r} would allow a whole top-level domain; name the domain (*.sendgrid.net)"
        )
    if not HOSTNAME.fullmatch(bare):
        if re.fullmatch(r"[0-9.]+", bare):
            raise ConfigError(
                f"egress_proxy.allow: {value!r} is an address; the proxy allows names. "
                "Put addresses in an infrastructure's `allow:` list instead"
            )
        raise ConfigError(
            f"egress_proxy.allow: {value!r} is not a host name (api.twilio.com) or a wildcard (*.sendgrid.net)"
        )
    return text


def _allow_list(allow) -> list:
    if isinstance(allow, str):
        allow = [allow]
    if not isinstance(allow, list) or not allow:
        raise ConfigError(
            'egress_proxy.allow must list at least one host name, e.g. [api.twilio.com, "*.sendgrid.net"]'
        )
    return allow


def _port(port) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(f"egress_proxy.port must be a number from 1 to 65535 (got {port!r})")
    return port


def _proxy_name(name) -> str:
    text = str(name or DEFAULT_NAME).strip().lower()
    if not HOSTNAME.fullmatch(text):
        raise ConfigError(f"egress_proxy.name {text!r} is not a host name (proxy.internal)")
    return text
