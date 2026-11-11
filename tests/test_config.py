"""Tests for askdata.config.load_config."""

from pathlib import Path

import pytest

from askdata.config import REPO_ROOT, Config, ConfigError, load_config

VALID_YAML = """
model:
  name: test-model
  temperature: 0
  rate_limit_retries: 2
  rate_limit_wait_seconds: 1.5
paths:
  database: data/test.db
  history: data/history.db
  raw_data: data/raw
  llm_cache: .cache/llm
  questions: eval/questions.jsonl
  results: eval/results
query:
  row_limit: 50
  timeout_seconds: 3
  sql_retries: 1
"""


def write_config(tmp_path: Path, text: str) -> Path:
    """Write YAML text to a config file inside tmp_path and return its path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text, encoding="utf-8")
    return config_file


def test_repository_config_loads() -> None:
    """The real configs/config.yaml in the repo must load without errors."""
    config = load_config()
    assert isinstance(config, Config)
    assert config.model.name != ""
    assert config.query.row_limit >= 1


def test_valid_config_values_and_types(tmp_path: Path) -> None:
    """Values are read with the right types (ints become floats where expected)."""
    config = load_config(write_config(tmp_path, VALID_YAML), base_dir=tmp_path)
    assert config.model.name == "test-model"
    assert config.model.temperature == 0.0
    assert isinstance(config.model.temperature, float)
    assert config.model.rate_limit_retries == 2
    assert config.query.row_limit == 50
    assert config.query.timeout_seconds == 3.0
    assert config.query.sql_retries == 1


def test_relative_paths_resolve_against_base_dir(tmp_path: Path) -> None:
    """Relative paths in the YAML become absolute paths under base_dir."""
    config = load_config(write_config(tmp_path, VALID_YAML), base_dir=tmp_path)
    assert config.paths.database == (tmp_path / "data" / "test.db").resolve()
    assert config.paths.database.is_absolute()


def test_default_base_dir_is_repo_root(tmp_path: Path) -> None:
    """Without base_dir, relative paths resolve against the repository root."""
    config = load_config(write_config(tmp_path, VALID_YAML))
    assert config.paths.results == (REPO_ROOT / "eval" / "results").resolve()


def test_missing_file_raises(tmp_path: Path) -> None:
    """A path that does not exist gives a ConfigError, not a raw OSError."""
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_missing_section_raises(tmp_path: Path) -> None:
    """Removing a whole section is reported by name."""
    text = VALID_YAML.replace("query:", "other:")
    with pytest.raises(ConfigError, match="'query'"):
        load_config(write_config(tmp_path, text), base_dir=tmp_path)


def test_missing_key_raises(tmp_path: Path) -> None:
    """Removing one key is reported with its full dotted name."""
    text = VALID_YAML.replace("  name: test-model\n", "")
    with pytest.raises(ConfigError, match="model.name"):
        load_config(write_config(tmp_path, text), base_dir=tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("row_limit: 50", "row_limit: fifty", "query.row_limit"),
        ("row_limit: 50", "row_limit: -1", "query.row_limit"),
        ("sql_retries: 1", "sql_retries: true", "query.sql_retries"),
        ("temperature: 0", "temperature: hot", "model.temperature"),
    ],
)
def test_wrong_type_raises(tmp_path: Path, old: str, new: str, key: str) -> None:
    """Wrong types and negative numbers are rejected."""
    text = VALID_YAML.replace(old, new)
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, text), base_dir=tmp_path)


def test_zero_row_limit_raises(tmp_path: Path) -> None:
    """A LIMIT of 0 would return nothing, so it is not allowed."""
    text = VALID_YAML.replace("row_limit: 50", "row_limit: 0")
    with pytest.raises(ConfigError, match="row_limit"):
        load_config(write_config(tmp_path, text), base_dir=tmp_path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    """Broken YAML gives a ConfigError."""
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write_config(tmp_path, "model: [unclosed"), base_dir=tmp_path)
