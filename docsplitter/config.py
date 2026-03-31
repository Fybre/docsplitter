"""Configuration loading: YAML files + environment variable overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


# ── Sub-models ────────────────────────────────────────────────────────────────


class AIConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    api_version: str | None = None   # set for Azure OpenAI; leave blank for standard OpenAI/local
    image_detail: Literal["low", "high", "auto"] = "auto"  # "low" is cheaper but can't read fine text
    max_tokens: int = 600
    timeout_seconds: int = 60
    max_retries: int = 3
    render_dpi: int = 150
    image_format: Literal["jpeg", "png"] = "jpeg"
    image_quality: int = 85


class OutputConfig(BaseModel):
    base_dir: str = "./output"
    filename_template: str = "{date}_{doc_type}_{doc_index:03d}.pdf"
    write_metadata_json: bool = True


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///./docsplitter.db"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class ChannelConfig(BaseModel):
    name: str
    type: Literal["watcher", "api"]
    output_subdir: str = "default"
    confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    type_hints: list[str] = Field(default_factory=list)

    # Split behaviour
    split_trigger_types: list[str] = Field(default_factory=list)
    # When non-empty, a new document boundary is only created when is_new_document=True
    # AND the detected document_type is in this list. All other pages are appended to
    # the preceding trigger document. Empty list = split on every detected boundary (default).

    # Watcher-only fields
    path: str | None = None
    stable_seconds: float = 2.0
    include_patterns: list[str] = Field(default_factory=lambda: ["*.pdf", "*.PDF"])

    @model_validator(mode="after")
    def watcher_requires_path(self) -> "ChannelConfig":
        if self.type == "watcher" and not self.path:
            raise ValueError(f"Channel '{self.name}': watcher channels require a 'path'")
        return self


# ── Root config ───────────────────────────────────────────────────────────────


class AppConfig(BaseModel):
    ai: AIConfig = Field(default_factory=AIConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    channels: list[ChannelConfig] = Field(default_factory=list)

    def channel(self, name: str) -> ChannelConfig:
        for ch in self.channels:
            if ch.name == name:
                return ch
        raise KeyError(f"Channel '{name}' not found in config")

    def api_channels(self) -> list[ChannelConfig]:
        return [ch for ch in self.channels if ch.type == "api"]

    def watcher_channels(self) -> list[ChannelConfig]:
        return [ch for ch in self.channels if ch.type == "watcher"]

    def redacted(self) -> dict[str, Any]:
        """Config dict safe to log (API key masked)."""
        d = self.model_dump()
        if d.get("ai", {}).get("api_key"):
            d["ai"]["api_key"] = "***"
        return d


# ── Loader ────────────────────────────────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base (override wins)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    Apply environment variables prefixed DOCSPLITTER_.
    Nested keys use double-underscore: DOCSPLITTER_AI__MODEL=gpt-4o
    sets data["ai"]["model"] = "gpt-4o".
    """
    prefix = "DOCSPLITTER_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return data


def load_config(
    default_path: str | Path = "config/default.yaml",
    local_path: str | Path = "config/local.yaml",
) -> AppConfig:
    default_path = Path(default_path)
    local_path = Path(local_path)

    data: dict[str, Any] = {}

    if default_path.exists():
        with open(default_path) as f:
            data = yaml.safe_load(f) or {}

    if local_path.exists():
        with open(local_path) as f:
            local_data = yaml.safe_load(f) or {}
        data = _deep_merge(data, local_data)

    data = _apply_env_overrides(data)

    return AppConfig.model_validate(data)


# Module-level singleton (populated by main.py on startup)
_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: AppConfig) -> None:
    global _config
    _config = cfg
