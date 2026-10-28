"""The one way a node downloads a file: from Gitea, over https that trusts the CA."""

import shlex

from outpost.client_config import PATH_CA


def download_from_gitea(url: str, out: str) -> str:
    """A command that downloads url to the file out. It fails on an HTTP error (-f).

    curl's flags: -f fail on an HTTP error, -s quiet, -S but still show errors, -L follow redirects.
    """
    return f"curl --cacert {PATH_CA} -fsSL {shlex.quote(url)} -o {shlex.quote(out)}"
