from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, load_catalog
from .paths import config_path, env_path


@dataclass
class ModelEndpoint:
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip())


@dataclass
class Config:
    bind_port: int = 8787
    classifier: str = "rules"
    notes_inbox: str = "~/Notes/inbox"
    agent_enabled: bool = True
    agent_command: list[str] = field(default_factory=lambda: ["omarchy", "agent", "prompt", "{prompt}"])
    herdr_command: list[str] = field(default_factory=lambda: ["herdr", "agent", "prompt", "default", "{prompt}"])
    wake_phrases: list[str] = field(default_factory=lambda: ["herd", "herdr"])
    token: str = ""
    local: ModelEndpoint = field(default_factory=ModelEndpoint)
    cloud: ModelEndpoint = field(default_factory=ModelEndpoint)
    action_overrides: dict = field(default_factory=dict)
    catalog: Catalog | None = None

    def notes_dir(self) -> Path:
        return Path(self.notes_inbox).expanduser()

    def endpoint_for(self, mode: str) -> ModelEndpoint:
        if mode == "local":
            return self.local
        if mode == "cloud":
            return self.cloud
        raise ValueError(f"not an HTTP classifier: {mode}")

    def actions(self) -> Catalog:
        if self.catalog is None:
            self.catalog = load_catalog(overrides=self.action_overrides)
        return self.catalog


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=value lines into os.environ without overwriting existing values."""
    target = path or env_path()
    if not target.is_file():
        return
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_secret(value: str, *env_names: str) -> str:
    raw = value.strip()
    if raw.startswith("${") and raw.endswith("}"):
        return os.environ.get(raw[2:-1], "")
    if raw.startswith("$") and raw[1:].replace("_", "").isalnum():
        return os.environ.get(raw[1:], "")
    if raw:
        return raw
    for name in env_names:
        found = os.environ.get(name, "").strip()
        if found:
            return found
    return ""


def load_config(path: Path | None = None) -> Config:
    load_dotenv()
    target = path or config_path()
    if not target.is_file():
        raise FileNotFoundError(f"Missing config: {target} (Start receiver in the widget)")
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    local = data.get("local") or {}
    cloud = data.get("cloud") or {}
    classifier = str(data.get("classifier") or "rules").strip().lower()
    if classifier not in {"rules", "local", "cloud"}:
        raise ValueError(f"classifier must be rules|local|cloud, got {classifier}")
    command = data.get("herdr_command") or ["herdr", "agent", "prompt", "default", "{prompt}"]
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError("herdr_command must be a list of strings")
    agent_command = data.get("agent_command") or ["omarchy", "agent", "prompt", "{prompt}"]
    if not isinstance(agent_command, list) or not all(isinstance(part, str) for part in agent_command):
        raise ValueError("agent_command must be a list of strings")
    phrases = data.get("wake_phrases") or ["herd", "herdr"]
    if not isinstance(phrases, list) or not all(isinstance(part, str) for part in phrases):
        raise ValueError("wake_phrases must be a list of strings")
    raw_actions = data.get("actions") or {}
    action_overrides: dict = {}
    if isinstance(raw_actions, dict):
        for name, values in raw_actions.items():
            if isinstance(values, dict):
                action_overrides[str(name)] = values
    return Config(
        bind_port=int(data.get("bind_port") or 8787),
        classifier=classifier,
        notes_inbox=str(data.get("notes_inbox") or "~/Notes/inbox"),
        agent_enabled=bool(data.get("agent_enabled", True)),
        agent_command=list(agent_command),
        herdr_command=list(command),
        wake_phrases=[part.strip() for part in phrases if str(part).strip()],
        token=str(data.get("token") or ""),
        local=ModelEndpoint(
            base_url=str(local.get("base_url") or ""),
            api_key=resolve_secret(str(local.get("api_key") or ""), "LOCAL_API_KEY", "OPENROUTER_API_KEY"),
            model=str(local.get("model") or ""),
        ),
        cloud=ModelEndpoint(
            base_url=str(cloud.get("base_url") or ""),
            api_key=resolve_secret(str(cloud.get("api_key") or ""), "OPENROUTER_API_KEY"),
            model=str(cloud.get("model") or ""),
        ),
        action_overrides=action_overrides,
    )
