"""Load and check the project configuration from configs/config.yaml.

The YAML file is turned into small, typed dataclasses so the rest of the code
can write ``config.query.row_limit`` instead of ``config["query"]["row_limit"]``,
and so a typo or a missing key fails early with a clear message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# src/askdata/config.py -> parents[0] = askdata, [1] = src, [2] = repository root.
# This works because the package is installed in editable mode (pip install -e .).
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH: Path = REPO_ROOT / "configs" / "config.yaml"


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed or has a wrong value."""


@dataclass(frozen=True)
class ModelConfig:
    """Settings for the Gemini model."""

    name: str
    temperature: float
    rate_limit_retries: int
    rate_limit_wait_seconds: float


@dataclass(frozen=True)
class PathsConfig:
    """File and folder locations, already resolved to absolute paths."""

    database: Path
    history: Path
    raw_data: Path
    llm_cache: Path
    questions: Path
    results: Path


@dataclass(frozen=True)
class QueryConfig:
    """Limits applied when generated SQL is validated and run."""

    row_limit: int
    timeout_seconds: float
    sql_retries: int


@dataclass(frozen=True)
class AppConfig:
    """Settings for the Streamlit app."""

    max_questions_per_session: int


@dataclass(frozen=True)
class Config:
    """The full project configuration."""

    model: ModelConfig
    paths: PathsConfig
    query: QueryConfig
    app: AppConfig


def _get_section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the sub-dictionary ``raw[name]``, or raise ConfigError if absent."""
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"Missing or invalid section '{name}' in config file.")
    return section


def _get_str(section: dict[str, Any], section_name: str, key: str) -> str:
    """Read a non-empty string value from a config section."""
    value = section.get(key)
    if not isinstance(value, str) or value.strip() == "":
        raise ConfigError(f"'{section_name}.{key}' must be a non-empty string, got {value!r}.")
    return value


def _get_int(section: dict[str, Any], section_name: str, key: str) -> int:
    """Read a non-negative integer value from a config section."""
    value = section.get(key)
    # bool is a subclass of int in Python, so `True` would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"'{section_name}.{key}' must be a non-negative integer, got {value!r}.")
    return value


def _get_float(section: dict[str, Any], section_name: str, key: str) -> float:
    """Read a non-negative number from a config section and return it as a float."""
    value = section.get(key)
    # Accept ints too (YAML reads `0` as an int), but reject bools for the reason above.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"'{section_name}.{key}' must be a non-negative number, got {value!r}.")
    return float(value)


def _get_path(section: dict[str, Any], section_name: str, key: str, base_dir: Path) -> Path:
    """Read a path from a config section, resolving relative paths against base_dir."""
    path = Path(_get_str(section, section_name, key))
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def load_config(path: Path | str | None = None, base_dir: Path | None = None) -> Config:
    """Read the YAML config file and return a checked Config object.

    Args:
        path: Config file to read. Defaults to configs/config.yaml in the repo.
        base_dir: Folder that relative paths are resolved against.
            Defaults to the repository root.

    Raises:
        ConfigError: If the file is missing, not valid YAML, or a value is wrong.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    root = base_dir if base_dir is not None else REPO_ROOT

    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"Config file is not valid YAML: {error}") from error

    if not isinstance(raw, dict):
        raise ConfigError("Config file must contain a mapping at the top level.")

    model_section = _get_section(raw, "model")
    paths_section = _get_section(raw, "paths")
    query_section = _get_section(raw, "query")
    app_section = _get_section(raw, "app")

    model = ModelConfig(
        name=_get_str(model_section, "model", "name"),
        temperature=_get_float(model_section, "model", "temperature"),
        rate_limit_retries=_get_int(model_section, "model", "rate_limit_retries"),
        rate_limit_wait_seconds=_get_float(model_section, "model", "rate_limit_wait_seconds"),
    )
    paths = PathsConfig(
        database=_get_path(paths_section, "paths", "database", root),
        history=_get_path(paths_section, "paths", "history", root),
        raw_data=_get_path(paths_section, "paths", "raw_data", root),
        llm_cache=_get_path(paths_section, "paths", "llm_cache", root),
        questions=_get_path(paths_section, "paths", "questions", root),
        results=_get_path(paths_section, "paths", "results", root),
    )
    query = QueryConfig(
        row_limit=_get_int(query_section, "query", "row_limit"),
        timeout_seconds=_get_float(query_section, "query", "timeout_seconds"),
        sql_retries=_get_int(query_section, "query", "sql_retries"),
    )

    app = AppConfig(
        max_questions_per_session=_get_int(app_section, "app", "max_questions_per_session"),
    )

    if query.row_limit == 0:
        raise ConfigError("'query.row_limit' must be at least 1.")
    if app.max_questions_per_session == 0:
        raise ConfigError("'app.max_questions_per_session' must be at least 1.")

    return Config(model=model, paths=paths, query=query, app=app)
