from __future__ import annotations

from pathlib import Path

PLUGIN_ID = "io.github.goooooooooody.omarchy-pebble-index"
APP_NAME = "omarchy-pebble-index"


def config_dir() -> Path:
    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.toml"


def env_path() -> Path:
    return config_dir() / ".env"


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def plugin_actions_dir() -> Path:
    return plugin_root() / "actions"


def user_actions_dir() -> Path:
    return config_dir() / "actions"


def community_action_dirs() -> list[Path]:
    """Other Omarchy plugins may ship `pebble-index/*.toml` next to their manifest."""
    root = Path.home() / ".config" / "omarchy" / "plugins"
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == PLUGIN_ID:
            continue
        extra = child / "pebble-index"
        if extra.is_dir():
            found.append(extra)
    return found


def action_search_paths() -> list[Path]:
    return [plugin_actions_dir(), *community_action_dirs(), user_actions_dir()]


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / APP_NAME


def data_dir() -> Path:
    return Path.home() / ".local" / "share" / APP_NAME


def db_path() -> Path:
    return data_dir() / "inbox.db"


def state_json_path() -> Path:
    return state_dir() / "state.json"
