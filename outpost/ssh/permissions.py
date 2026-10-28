"""A file's owner and permissions, in words, for the logs.

Permissions ("mode" in Unix) are 3 octal digits: what the owner, the group, and everyone else may do.
Each digit is a sum of: read = 4, write = 2, execute = 1.

    0644  ->  owner may read, write (4+2); group may read (4); others may read (4)
"""

import grp
import pwd
from pathlib import Path


def permissions_in_words(owner: str, group: str, mode: int) -> str:
    """Describe who may do what with a file.

    Args:
        owner: the owner's user name, such as "root".
        group: the group's name, such as "root".
        mode: the permissions as a number, such as 0o644.

    Returns:
        For example "owner root may read, write; group root may read; others may read (0644)".
    """
    # Pick each digit out of the number: shift it to the right end (>>), keep its 3 bits (& 7).
    owner_bits, group_bits, others_bits = mode >> 6 & 7, mode >> 3 & 7, mode & 7
    return (
        f"owner {owner} {_may(owner_bits)}; group {group} {_may(group_bits)}; "
        f"others {_may(others_bits)} ({mode & 0o7777:04o})"
    )


def permissions_in_words_from_stat(stdout: str) -> str:
    """permissions_in_words() for a file on the server.

    Args:
        stdout: output whose last line is what commands.print_owner_and_permissions() printed,
            such as "root root 644".
    """
    owner, group, octal = stdout.strip().splitlines()[-1].split()
    return permissions_in_words(owner, group, int(octal, 8))  # int(..., 8): read "644" as octal


def permissions_in_words_of_local(file: Path) -> str:
    """permissions_in_words() for a file on this machine."""
    info = file.stat()
    owner = pwd.getpwuid(info.st_uid).pw_name  # user number -> user name
    group = grp.getgrgid(info.st_gid).gr_name  # group number -> group name
    return permissions_in_words(owner, group, info.st_mode)


def _may(bits: int) -> str:
    """One permission digit in words: 6 -> "may read, write", 0 -> "may do nothing"."""
    allowed = [word for word, bit in (("read", 4), ("write", 2), ("execute", 1)) if bits & bit]
    return "may " + ", ".join(allowed) if allowed else "may do nothing"
