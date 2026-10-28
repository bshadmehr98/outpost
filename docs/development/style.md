# Code style

How Outpost's code is written. The model is the `outpost/ssh/` package: when in doubt, read it and
do the same. Use the words in the [glossary](../glossary.md).

The tools check the easy parts for you:

```
scripts/format.sh            format the code and sort the imports (ruff)
ruff check outpost tests     lint
pyright                      types
python -m pytest -q          unit tests
```

To run the format and lint before every commit, install the git hook once in your clone:
`pip install pre-commit && pre-commit install`. The rules are in `.pre-commit-config.yaml`.

## Names

A name says what the thing does. A long clear name is better than a short vague one.

```python
# good
def permissions_in_words(owner: str, group: str, mode: int) -> str: ...
def _run_with_ssh(self, command: str, stdin: bytes, label: str) -> Result: ...
def _port_accepts_connections(port: int) -> bool: ...

# bad
def perms(o, g, m): ...
def _exec(self, cmd, data, lbl): ...
def _check(port): ...
```

- A function that answers yes or no reads like a question: `ssh_logged_in()`, `command_finished()`.
- A function that builds a shell command says what the command does: `print_owner_and_permissions()`.
- A constant is named for what it holds: `MARKER_CONNECTED`, `ADDRESS_ON_DEFAULT_ROUTE`,
  not `CMD2` or `DEFAULT`.
- Private names start with `_`. Code outside a package uses only what its `__init__.py` exports.

## Order: top-down

Public things first. Helpers come below the code that calls them. You read a file from the top
and meet the big idea before the details.

```python
class Host:
    def run(self, ...): ...          # public, first
    def write(self, ...): ...
    def _run_with_ssh(self, ...): ...  # private, at the bottom of the class

def _first_line(script: str) -> str:  # module helper, below its caller
    ...
```

Group related functions under a short section line: `# -- files ------------------`.

## One idea per file

A file holds one idea. When a module holds several ideas, make it a package. Its `__init__.py` has:

1. a short overview: what the package does, in a few lines;
2. a usage example;
3. a map of its files, one line each;
4. the re-exports of the public names, with `__all__`.

See `outpost/ssh/__init__.py`:

```python
"""Run commands and copy files on a server, over ssh and scp.

    host = Host("gitea-1")
    host.run("apt-get update")

    errors.py       Result, and the errors: SshError, Unreachable, CommandFailed
    host.py         Host
    ...
"""

from outpost.ssh.errors import CommandFailed, Result, SshError, Unreachable
from outpost.ssh.host import Host

__all__ = ["CommandFailed", "Host", "Result", "SshError", "Unreachable"]
```

## Small named steps instead of comments

If a block needs a comment to say what it does, give it a name instead: a variable or a function.

```python
# good
save_stdin_to_file = all_of([f"mkdir -p {folder}", f"cat > {file}", f"chmod {mode} {file}"])
result = self._run_with_ssh(f"bash -c {shlex.quote(save_stdin_to_file)}", content, label=...)

# bad
# make the folder, write the file, set its mode
cmd = f"mkdir -p {folder} && cat > {file} && chmod {mode} {file}"
r = self._run_with_ssh(f"bash -c {shlex.quote(cmd)}", content, label=...)
```

## Comments

Write a comment only for what a name can't say:

- what a shell flag does: `"-N",  # run no command on the server; only forward the port`
- Python that is not obvious: `int(octal, 8)  # read "644" as octal`
- why something is done a surprising way, or a known flaw.

Don't repeat the code in words. Don't leave old code in comments.

## Docstrings

Every public module, class and function has a docstring. The first line is one sentence: what it
does. Add `Args:`, `Returns:` and `Raises:` when they help the caller; skip them when the first
line already says it all.

```python
def run(self, script: str, raise_on_failure: bool = False, timeout: int = 1800) -> Result:
    """Run a bash script on the server, as root.

    Args:
        script: the bash script.
        raise_on_failure: if True, a failing script raises CommandFailed instead of returning.
        timeout: seconds before giving up on the script (default 30 minutes).

    Returns:
        The Result. Check result.ok to see if it worked.

    Raises:
        Unreachable: ssh could not log in, or the connection broke.
        CommandFailed: only with raise_on_failure=True, if the script failed.
    """
```

Write in plain, simple English. Short sentences.

## Flags and defaults

A flag's name says what it turns on, and its default is the unsurprising one.

```python
# good: the name says what True does
host.run(script, raise_on_failure=True)

# bad: does check=True check something, or raise? And is it on by default?
host.run(script, check=True)
```

## No backward-compat aliases

When you rename or move something, update every caller in the same change. Never keep the old name
"for now":

```python
# bad
run_ssh = _run_with_ssh   # old name, kept for callers
from outpost.ssh import *  # so old imports still work
```

## Logging

Each module gets its own logger, and logs every call to the outside world (ssh, scp, HTTP, a
subprocess) at DEBUG, with what it did, how it ended and how long it took.

```python
logger = logging.getLogger(__name__)

logger.debug("[%s] %s: command exit %d (%.1fs)", self.alias, label, code, seconds)
```

Use `%s` arguments, not f-strings, in log calls. `print()` is for the CLI's own output only.

## Errors

Each package has a small error hierarchy, with one base class. Callers catch the base class to
catch them all, or one subclass for one case. List the tree in the docstring of `errors.py`.

```python
"""
SshError                 base class: `except SshError` catches both below
├── Unreachable          ssh failed: the command did not run, or did not finish
└── CommandFailed        the command ran and failed; `.result` has its output
"""

class SshError(Exception):
    """Base class for every error in outpost.ssh. Catch this to catch them all."""
```

An error message says which server or file, what went wrong, and what to do if that is known:
`f"[{self.alias}] could not detect an IP address; set it in servers.yml"`.

## Types

Use type hints on every public function. Use the modern forms: `str | None`, `list[str]`,
`dict[str, int]`. pyright (basic mode) checks them.
