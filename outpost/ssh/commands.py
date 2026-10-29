"""The shell commands Host sends to the server, and two helpers to join commands."""

# -- joining commands -------------------------------------------------------------------


def all_of(steps: list[str]) -> str:
    """Run the steps one after another, and stop at the first one that fails.

    all_of(["mkdir -p /a", "cat > /a/b"])  ->  "mkdir -p /a && cat > /a/b"
    (In the shell, `x && y` runs y only if x worked.)
    """
    return " && ".join(steps)


def first_of(tries: list[str]) -> str:
    """Try each command in turn, until one works.

    first_of(["hostname -I", "ip route get 1"])  ->  "hostname -I || ip route get 1"
    (In the shell, `x || y` runs y only if x failed.)
    """
    return " || ".join(tries)


# -- files ------------------------------------------------------------------------------


def print_owner_and_permissions(file: str) -> str:
    """A command that prints a file's owner, group and permissions, such as "root root 644".

    `stat -c FORMAT` prints only what FORMAT asks for:
        %U  the owner's name        %G  the group's name        %a  the permissions, in octal
    permissions.permissions_in_words_from_stat() turns that line into words for the logs.
    """
    return f"stat -c '%U %G %a' {file}"


# -- the server's address ---------------------------------------------------------------

# `hostname -I` prints all the server's addresses on one line, such as "10.0.0.5 172.17.0.1".
# `2>/dev/null` hides its error messages; `awk '{print $1}'` keeps only the first word.
FIRST_ADDRESS = "hostname -I 2>/dev/null | awk '{print $1}'"

# For systems without `hostname -I` (some small Linux images).
# `ip -4 route get 1` asks "how would a packet to 1.0.0.0 leave?", and prints for example
#     1.0.0.0 via 10.0.0.1 dev eth0 src 10.0.0.5 uid 0      (through a gateway)
#     1.0.0.0 dev eth0 src 10.0.0.5 uid 0                   (no gateway: the network is directly attached)
# The address after `src` is the one this server sends from. Its place on the line differs, so awk
# looks at each word ($i, for i from 1 to NF, the number of words) and prints the one after `src`.
# `exit` stops after the first line.
ADDRESS_ON_DEFAULT_ROUTE = (
    "ip -4 route get 1 | awk '{for (i = 1; i < NF; i++) if ($i == \"src\") {print $(i + 1); exit}}'"
)

# -- facts about the server -------------------------------------------------------------

# Prints one "key=value" line per fact; Host.facts() reads them into a dict.
#   . /etc/os-release   `.` loads the file's lines (ID=ubuntu, VERSION_ID=24.04, ...) as variables.
#   ${X:-}              gives "" when the file has no X. A plain $X would stop the script,
#                       because Host.run() turns on `set -u` (stop on unset variables).
#   $(...)              runs a command and puts what it printed in its place.
PRINT_FACTS = "\n".join(
    [
        ". /etc/os-release",
        'echo "id=$ID"',  # ubuntu, debian, alpine, rocky, ...
        'echo "id_like=${ID_LIKE:-}"',  # the family: "debian", "rhel fedora", ...
        'echo "version_id=${VERSION_ID:-}"',  # 24.04, 12, ...
        'echo "codename=${VERSION_CODENAME:-}"',  # noble, bookworm, ...
        'echo "arch=$(uname -m)"',  # x86_64, aarch64, ...
        f'echo "ip=$({FIRST_ADDRESS})"',
        'echo "hostname=$(hostname)"',
    ]
)
