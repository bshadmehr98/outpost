"""The one error of outpost.config.

ConfigError     a problem in servers.yml, mirrors.yml, a needs file or a check, worded for humans
"""


class ConfigError(Exception):
    """A problem in what the user wrote (servers.yml, mirrors.yml, needs, checks), worded for humans."""
