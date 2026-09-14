"""Configuration paths and loaders for Milo freshness."""

from __future__ import annotations

import json
import os
from pathlib import Path


STATE_DIR = Path.home() / ".local" / "state" / "milo"
DEFAULT_DB = STATE_DIR / "freshness.db"
DEFAULT_CONFIG = STATE_DIR / "freshness.json"
DEFAULT_FEEDS = Path(__file__).with_name("feeds.json")


def database_path(db=None):
    """Return an explicit path, the environment override, or the state default."""
    if db is not None:
        return Path(db).expanduser()
    override = os.environ.get("MILO_FRESHNESS_DB")
    return Path(override).expanduser() if override else DEFAULT_DB


def load_config(path=None):
    """Load optional local settings without creating a configuration file."""
    settings = {"lat": 0.0, "lon": 0.0, "disabled_feeds": []}
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, json.JSONDecodeError, TypeError):
        return settings
    if not isinstance(loaded, dict):
        return settings
    try:
        settings["lat"] = float(loaded.get("lat", settings["lat"]))
        settings["lon"] = float(loaded.get("lon", settings["lon"]))
    except (TypeError, ValueError):
        pass
    disabled = loaded.get("disabled_feeds", loaded.get("disabled feed names", []))
    if isinstance(disabled, list):
        settings["disabled_feeds"] = [str(name) for name in disabled]
    return settings


def load_feeds(path=None):
    """Load the bundled proposal or another feed list with the same shape."""
    feed_path = Path(path).expanduser() if path is not None else DEFAULT_FEEDS
    loaded = json.loads(feed_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("feed configuration must be a JSON list")
    return loaded
