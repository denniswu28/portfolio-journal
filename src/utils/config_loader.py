"""
config_loader.py - Load configuration from YAML files.

Provides helpers to load:
  - settings.yaml  (application settings)
  - persistent_context.yaml  (user investment strategy)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from src.data_ingestion.models import PersistentContext


def load_yaml(filepath: str | Path) -> Dict[str, Any]:
    """
    Load a YAML file and return its contents as a dict.

    Args:
        filepath: Path to the YAML file.

    Returns:
        Parsed YAML contents.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {filepath}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_dir: str | Path = "config") -> Dict[str, Any]:
    """
    Load application settings from config/settings.yaml.

    Args:
        config_dir: Directory containing the settings.yaml file.

    Returns:
        Settings dict.
    """
    return load_yaml(Path(config_dir) / "settings.yaml")


def load_persistent_context(config_dir: str | Path = "config") -> PersistentContext:
    """
    Load user strategy and constraints from config/persistent_context.yaml.

    Args:
        config_dir: Directory containing the persistent_context.yaml file.

    Returns:
        A PersistentContext model instance.
    """
    data = load_yaml(Path(config_dir) / "persistent_context.yaml")
    return PersistentContext.model_validate(data)
