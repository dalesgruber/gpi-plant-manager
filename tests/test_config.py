from pathlib import Path

import pytest

from zira_probe.config import Config, load_config


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_config_reads_env_and_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("ZIRA_API_KEY", raising=False)
    monkeypatch.delenv("ZIRA_BASE_URL", raising=False)
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_API_KEY=key-abc123\nZIRA_BASE_URL=https://api.zira.us/public/\n")

    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources:
    - meter_id: "100"
      label: "Real DS 1"
  channels:
    - channel_id: "200"
      label: "Channel 1"
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    config = load_config(env_path=env_path, yaml_path=yaml_path)

    assert isinstance(config, Config)
    assert config.api_key == "key-abc123"
    assert config.base_url == "https://api.zira.us/public/"
    assert config.test_ds_meter_id == "999"
    assert config.test_ds_number_metric == "1"
    assert config.test_ds_text_metric == "2"
    assert len(config.read_data_sources) == 1
    assert config.read_data_sources[0].meter_id == "100"
    assert config.read_channels[0].channel_id == "200"
    assert config.read_window_days == 7
    assert config.undocumented_timeout_seconds == 10


def test_load_config_missing_api_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ZIRA_API_KEY", raising=False)
    monkeypatch.delenv("ZIRA_BASE_URL", raising=False)
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_BASE_URL=https://api.zira.us/public/\n")
    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources: []
  channels: []
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    with pytest.raises(RuntimeError, match="ZIRA_API_KEY"):
        load_config(env_path=env_path, yaml_path=yaml_path)


def test_load_config_default_base_url(tmp_path, monkeypatch):
    monkeypatch.delenv("ZIRA_API_KEY", raising=False)
    monkeypatch.delenv("ZIRA_BASE_URL", raising=False)
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_API_KEY=key-abc123\n")
    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources: []
  channels: []
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    config = load_config(env_path=env_path, yaml_path=yaml_path)

    assert config.base_url == "https://api.zira.us/public/"


def test_load_config_does_not_override_ambient_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIRA_API_KEY", "ambient-key")
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_API_KEY=dotfile-key\n")
    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources: []
  channels: []
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    config = load_config(env_path=env_path, yaml_path=yaml_path)

    assert config.api_key == "ambient-key"
