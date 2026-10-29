"""Read a YAML file whose top level must be a mapping."""

from pathlib import Path

import yaml

from outpost.config.errors import ConfigError


def read_yaml_mapping(path: Path) -> dict:
    """The mapping in a YAML file; an empty file is an empty mapping.

    Raises:
        ConfigError: the file is not valid YAML, or its top level is not a mapping.
    """
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return data
