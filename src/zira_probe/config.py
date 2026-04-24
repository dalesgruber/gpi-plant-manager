"""Configuration loader: merges .env secrets with config.yaml structure."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.zira.us/public/"


@dataclass(frozen=True)
class ReadDataSource:
    meter_id: str
    label: str


@dataclass(frozen=True)
class ReadChannel:
    channel_id: str
    label: str


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    test_ds_meter_id: str
    test_ds_number_metric: str
    test_ds_text_metric: str
    read_data_sources: list[ReadDataSource] = field(default_factory=list)
    read_channels: list[ReadChannel] = field(default_factory=list)
    read_window_days: int = 7
    undocumented_timeout_seconds: int = 10


def load_config(
    env_path: Path | str = ".env",
    yaml_path: Path | str = "config.yaml",
) -> Config:
    env_path = Path(env_path)
    yaml_path = Path(yaml_path)

    load_dotenv(dotenv_path=env_path, override=False)

    api_key = os.environ.get("ZIRA_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"ZIRA_API_KEY missing. Set it in {env_path} (see .env.example)."
        )

    base_url = os.environ.get("ZIRA_BASE_URL", DEFAULT_BASE_URL)

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    test_ds = raw["test_data_source"]
    targets = raw["read_targets"]
    settings = raw["probe_settings"]

    return Config(
        api_key=api_key,
        base_url=base_url,
        test_ds_meter_id=str(test_ds["meter_id"]),
        test_ds_number_metric=str(test_ds["metrics"]["number"]),
        test_ds_text_metric=str(test_ds["metrics"]["text"]),
        read_data_sources=[
            ReadDataSource(meter_id=str(d["meter_id"]), label=str(d["label"]))
            for d in targets.get("data_sources", [])
        ],
        read_channels=[
            ReadChannel(channel_id=str(c["channel_id"]), label=str(c["label"]))
            for c in targets.get("channels", [])
        ],
        read_window_days=int(settings["read_window_days"]),
        undocumented_timeout_seconds=int(settings["undocumented_timeout_seconds"]),
    )
