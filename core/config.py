"""
core/config.py — Load config from config.yaml (ported from review-phim).
Supports: YAML loading, env override, dot-notation access.
"""
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "project": {"output_dir": "Projects", "log_dir": "logs", "temp_dir": "temp"},
    "veo3": {
        "multi_video": 3, "output_count": 1, "aspect_ratio": "16:9",
        "create_image_model": "Nano Banana pro",
        "wait_gen_image": 15, "retry_with_error": 5,
        "wait_resend_image": 30, "token_retry": 5,
        "token_retry_delay": 3, "clear_data_token_image": 50,
        "clear_data_wait": 4, "image_response_timeout": 80,
        "seed_mode": "Random", "seed_value": 9797,
    },
    "llm": {
        "provider": "gemini", "model": "gemini-2.5-flash",
        "temperature": 0.9, "max_tokens": 8192,
        "batch_size": 20, "max_retries": 5,
    },
    "tts": {
        "provider": "kokoro", "language": "en", "voice": "af_heart",
        "speed": 1.0, "delivery_style": "expressive",
    },
    "video": {"resolution": "1920x1080", "fps": 30, "layout": "character_drama"},
    "shorts": {"enabled": True, "hook_test_blocks": 3, "block_duration": 8, "main_trailer_blocks": 5},
    "api_keys": {"gemini": "", "openai": ""},
}


class _ConfigDict(dict):
    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' has no key '{key}'")
        if isinstance(value, dict):
            return _ConfigDict(value)
        return value

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        return {}
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _override_from_env(config: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "GEMINI_API_KEYS": ("api_keys", "gemini"),
        "OPENAI_API_KEY": ("api_keys", "openai"),
        "VEO3_OUTPUT_COUNT": ("veo3", "output_count"),
        "VEO3_ASPECT_RATIO": ("veo3", "aspect_ratio"),
        "LLM_PROVIDER": ("llm", "provider"),
        "LLM_MODEL": ("llm", "model"),
    }
    for env_var, (section, key) in env_map.items():
        value = os.environ.get(env_var, "").strip()
        if not value:
            continue
        if value.isdigit():
            value = int(value)
        elif value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if section in config:
            config[section][key] = value
    return config


def load_config(config_path: Path | str | None = None) -> _ConfigDict:
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    elif isinstance(config_path, str):
        config_path = Path(config_path)
    file_config = _load_yaml(config_path)
    merged = _deep_merge(DEFAULT_CONFIG, file_config)
    merged = _override_from_env(merged)
    return _ConfigDict(merged)


_config_singleton: _ConfigDict | None = None


def get_config() -> _ConfigDict:
    global _config_singleton
    if _config_singleton is None:
        _config_singleton = load_config()
    return _config_singleton


def reload_config(config_path=None) -> _ConfigDict:
    global _config_singleton
    _config_singleton = load_config(config_path)
    return _config_singleton
