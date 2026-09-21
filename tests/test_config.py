"""Config loader tests: defaults, file loading, unknown-key rejection, env fallback."""

import os

import pytest

from jev_mobile.config import DEFAULTS, load_config


def write_config(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(str(tmp_path / "missing.yaml"))
    assert config == DEFAULTS


def test_file_values_and_coercion(tmp_path):
    path = write_config(
        tmp_path,
        "task: ' 打开设置 '\n"
        "adb_path: 'C:\\\\platform-tools\\\\adb.exe'\n"
        "device: 127.0.0.1:16416\n"
        "action_interval: 1.5\n"
        "screenshots: true\n",
    )
    config = load_config(str(path))
    assert config["task"] == "打开设置"
    assert config["device"] == "127.0.0.1:16416"
    assert config["action_interval"] == 1.5
    assert config["screenshots"] is True
    assert config["record_dir"] is None  # untouched default


def test_unknown_keys_are_rejected(tmp_path):
    path = write_config(tmp_path, "task: hi\ntasks_typo: 1\n")
    with pytest.raises(ValueError, match="tasks_typo"):
        load_config(str(path))


def test_env_fallback_only_when_file_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTION_INTERVAL", "2")
    path = write_config(tmp_path, "task: hi\n")
    assert load_config(str(path))["action_interval"] == 2.0
    path2 = write_config(tmp_path, "task: hi\naction_interval: 0.5\n")
    assert load_config(str(path2))["action_interval"] == 0.5


def test_non_mapping_file_is_rejected(tmp_path):
    path = write_config(tmp_path, "- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        load_config(str(path))
