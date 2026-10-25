"""
Configuration management for LMArenaBridge.
Handles loading, saving, and managing configuration.
"""

import copy
import json
import math
import os
from typing import Optional, Dict, Any
from collections import defaultdict

from . import constants
from .logging_utils import get_logger

_LOGGER = get_logger("config")


# Global state
_current_config_file: str = constants.CONFIG_FILE
_current_token_index: int = 0


def _main_attr(name: str) -> Optional[str]:
    """Best-effort read of a path global from ``src.main`` (single source of truth).

    ``main.CONFIG_FILE`` / ``main.MODELS_FILE`` are what tests patch; the config
    module must follow them or file I/O silently targets the real
    ``./config.json`` / ``./models.json`` (observed: test runs clobbering a
    user's live config). Lazy import avoids a hard cycle at import time.
    """
    try:
        from . import main as _main

        value = getattr(_main, name, None)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _resolve_config_file() -> str:
    return _main_attr("CONFIG_FILE") or _current_config_file


def _resolve_models_file() -> str:
    return _main_attr("MODELS_FILE") or constants.MODELS_FILE


def get_config_file() -> str:
    """Get the current config file path."""
    return _resolve_config_file()


def set_config_file(path: str) -> None:
    """Set the config file path (useful for tests). Keeps ``main`` in sync."""
    global _current_config_file, _current_token_index
    if _current_config_file != path:
        _current_config_file = path
        _current_token_index = 0
    try:
        from . import main as _main

        _main.CONFIG_FILE = path
    except Exception:
        pass


def get_config() -> dict:
    """
    Load configuration from file with defaults.
    Returns a dictionary with all configuration values.
    """
    global _current_token_index

    config_file = _resolve_config_file()

    # Reset token index if config file changed
    if _current_token_index != 0:
        global_state = _get_global_state()
        if global_state.get("_last_config_file") != config_file:
            _current_token_index = 0

    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    except Exception:
        config = {}

    if not isinstance(config, dict):
        config = {}

    # Ensure default keys exist and normalize known fields to safe types.
    _apply_config_defaults(config)

    return config


_CONFIG_BOOL_FIELDS = {
    "prune_invalid_tokens": False,
    "persist_arena_auth_cookie": True,
    "allow_missing_api_key": False,
}
_CONFIG_OPTIONAL_BOOL_FIELDS = {
    "userscript_proxy_prefill_cached_recaptcha",
    "camoufox_fetch_headless",
    "camoufox_proxy_headless",
    "camoufox_proxy_persistent_context",
}
_CONFIG_OPTIONAL_STRING_FIELDS = {
    "cf_bm",
    "cfuvid",
    "provisional_user_id",
    "user_agent",
    "userscript_proxy_secret",
    "recaptcha_sitekey",
    "recaptcha_action",
    "next_action_upload",
    "next_action_signed_url",
    "camoufox_proxy_user_data_dir",
}
# field -> (default, minimum, maximum, integer?)
_CONFIG_OPTIONAL_NUMBER_FIELDS = {
    "stream_total_timeout_seconds": (600.0, 30.0, 3600.0, False),
    "chrome_fetch_recaptcha_max_attempts": (5, 1, 20, True),
    "chrome_fetch_outer_timeout_seconds": (120.0, 20.0, 300.0, False),
    "camoufox_fetch_outer_timeout_seconds": (180.0, 20.0, 300.0, False),
    "camoufox_proxy_launch_timeout_seconds": (90.0, 20.0, 300.0, False),
    "userscript_proxy_poll_timeout_seconds": (25, 1, 300, True),
    "userscript_proxy_job_ttl_seconds": (90, 10, 600, True),
    "userscript_proxy_grace_seconds": (0.5, 0.0, 2.0, False),
    "userscript_proxy_pickup_timeout_seconds": (10.0, 0.5, 15.0, False),
    "userscript_proxy_status_timeout_seconds": (30.0, 5.0, 300.0, False),
    "userscript_proxy_preflight_timeout_seconds": (30.0, 5.0, 600.0, False),
    "userscript_proxy_signup_preflight_timeout_seconds": (240.0, 5.0, 900.0, False),
    "userscript_proxy_recaptcha_grace_seconds": (25.0, 0.0, 90.0, False),
}
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off", ""}


def _coerce_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    return None


def _coerce_number(value, *, default, minimum, maximum, integer):
    """Return (bounded number, repaired?). Bools are not numbers here."""
    fallback = int(default) if integer else float(default)
    if isinstance(value, bool):
        return fallback, True
    try:
        number = float(value)
    except Exception:
        return fallback, True
    if not math.isfinite(number):
        return fallback, True
    bounded = max(float(minimum), min(float(maximum), number))
    return (int(round(bounded)) if integer else float(bounded)), bounded != number


def normalize_config(config: object) -> tuple[dict, list[str]]:
    """Normalize known fields, preserve unknowns, and return safe warnings.

    Warnings contain field names/counts only—never passwords, tokens, or keys.
    """
    warnings: list[str] = []
    if not isinstance(config, dict):
        warnings.append("top-level config must be a JSON object; defaults were used")
        source: dict = {}
    else:
        source = copy.deepcopy(config)
    result: dict = dict(source)

    password = result.get("password", "admin")
    if isinstance(password, str) and password.strip():
        result["password"] = password
    else:
        result["password"] = "admin"
        warnings.append("password was missing, blank, or not a string; default was used")

    for field in ("auth_token", "cf_clearance"):
        value = result.get(field, "")
        if isinstance(value, str):
            result[field] = value.strip()
        else:
            result[field] = ""
            warnings.append(f"{field} was not a string; empty value was used")

    raw_tokens = result.get("auth_tokens", [])
    if isinstance(raw_tokens, str):
        warnings.append("auth_tokens was a single string; converted to a one-item list")
        raw_tokens = [raw_tokens]
    tokens: list[str] = []
    if isinstance(raw_tokens, list):
        removed = 0
        seen: set[str] = set()
        for token in raw_tokens:
            if not isinstance(token, str) or not token.strip() or token.strip() in seen:
                removed += 1
                continue
            cleaned = token.strip()
            seen.add(cleaned)
            tokens.append(cleaned)
        if removed:
            warnings.append(f"auth_tokens had {removed} blank, duplicate, or non-string entries removed")
    else:
        warnings.append("auth_tokens was not a list; empty list was used")
    result["auth_tokens"] = tokens

    if "browser_cookies" in result:
        raw_cookies = result.get("browser_cookies")
        if isinstance(raw_cookies, dict):
            cookies = {
                name: value
                for name, value in raw_cookies.items()
                if isinstance(name, str) and isinstance(value, str)
            }
            if len(cookies) != len(raw_cookies):
                warnings.append("browser_cookies had non-string entries removed")
            result["browser_cookies"] = cookies
        else:
            result.pop("browser_cookies", None)
            warnings.append("browser_cookies was not an object; it was removed")

    raw_api_keys = result.get("api_keys", [])
    api_keys: list[dict] = []
    if isinstance(raw_api_keys, list):
        repaired = 0
        for entry in raw_api_keys:
            if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not entry["key"].strip():
                repaired += 1
                continue
            item = dict(entry)
            item["key"] = item["key"].strip()
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                item["name"] = "Unnamed Key"
                repaired += 1
            item["rpm"], changed = _coerce_number(
                item.get("rpm", constants.DEFAULT_RATE_LIMIT_RPM),
                default=constants.DEFAULT_RATE_LIMIT_RPM,
                minimum=1,
                maximum=100000,
                integer=True,
            )
            repaired += int(changed and "rpm" in entry)
            item["created"], changed = _coerce_number(
                item.get("created", 1704236400),
                default=1704236400,
                minimum=0,
                maximum=4102444800,
                integer=True,
            )
            repaired += int(changed and "created" in entry)
            api_keys.append(item)
        if repaired:
            warnings.append(f"api_keys had {repaired} invalid or repaired entries")
    else:
        warnings.append("api_keys was not a list; empty list was used")
    result["api_keys"] = api_keys

    raw_usage = result.get("usage_stats", {})
    usage: dict[str, int] = {}
    if isinstance(raw_usage, dict):
        repaired = 0
        for model, count in raw_usage.items():
            if not isinstance(model, str) or isinstance(count, bool):
                repaired += 1
                continue
            try:
                numeric = float(count)
            except Exception:
                repaired += 1
                continue
            if not math.isfinite(numeric):
                repaired += 1
                continue
            usage[model] = max(0, int(numeric))
            repaired += int(numeric < 0)
        if repaired:
            warnings.append(f"usage_stats had {repaired} invalid or repaired entries")
    else:
        warnings.append("usage_stats was not an object; empty object was used")
    result["usage_stats"] = usage

    for field, default in _CONFIG_BOOL_FIELDS.items():
        if field not in result:
            result[field] = default
            continue
        coerced = _coerce_bool(result.get(field))
        if coerced is None:
            result[field] = default
            warnings.append(f"{field} was not a recognized boolean; default was used")
        else:
            result[field] = coerced

    for field in _CONFIG_OPTIONAL_BOOL_FIELDS:
        if field not in result:
            continue
        coerced = _coerce_bool(result.get(field))
        if coerced is None:
            result.pop(field, None)
            warnings.append(f"{field} was not a recognized boolean; runtime default will be used")
        else:
            result[field] = coerced

    window_defaults = {
        "camoufox_proxy_window_mode": constants.DEFAULT_CAMOUFOX_PROXY_WINDOW_MODE,
        "camoufox_fetch_window_mode": constants.DEFAULT_CAMOUFOX_FETCH_WINDOW_MODE,
        "chrome_fetch_window_mode": constants.DEFAULT_CHROME_FETCH_WINDOW_MODE,
    }
    valid_modes = {str(mode).lower() for mode in constants.VALID_WINDOW_MODES}
    for field, default in window_defaults.items():
        value = result.get(field, default)
        if isinstance(value, str) and value.strip().lower() in valid_modes:
            result[field] = value.strip().lower()
        else:
            result[field] = default
            if field in source:
                warnings.append(f"{field} was not a valid window mode; default was used")

    for field in _CONFIG_OPTIONAL_STRING_FIELDS:
        if field not in result:
            continue
        value = result.get(field)
        if isinstance(value, str):
            result[field] = value.strip()
        else:
            result.pop(field, None)
            warnings.append(f"{field} was not a string; it was removed")

    for field, (default, minimum, maximum, integer) in _CONFIG_OPTIONAL_NUMBER_FIELDS.items():
        if field not in result:
            continue
        result[field], changed = _coerce_number(
            result.get(field), default=default, minimum=minimum, maximum=maximum, integer=integer
        )
        if changed:
            warnings.append(f"{field} was invalid or out of range; a bounded value was used")

    return result, warnings


def validate_config(config: object) -> list[str]:
    """Return value-free schema warnings without mutating *config*."""
    return normalize_config(config)[1]


def _apply_config_defaults(config: dict) -> list[str]:
    """Normalize *config* in place and return value-free schema warnings."""
    normalized, warnings = normalize_config(config)
    if isinstance(config, dict):
        config.clear()
        config.update(normalized)
    try:
        _global_state["config_warnings"] = list(warnings)
    except Exception:
        pass
    return warnings

def save_config(config: dict, *, preserve_auth_tokens: bool = True) -> None:
    """
    Save configuration to file.
    
    Args:
        config: Configuration dictionary to save
        preserve_auth_tokens: If True, don't overwrite auth tokens from disk
    """
    config_file = _resolve_config_file()
    try:
        if preserve_auth_tokens:
            try:
                with open(config_file, "r") as f:
                    on_disk = json.load(f)
            except Exception:
                on_disk = None

            if isinstance(on_disk, dict):
                if "auth_tokens" in on_disk and isinstance(on_disk.get("auth_tokens"), list):
                    config["auth_tokens"] = list(on_disk.get("auth_tokens") or [])
                if "auth_token" in on_disk:
                    config["auth_token"] = str(on_disk.get("auth_token") or "")

        # usage_stats will be set by the caller
        _apply_config_defaults(config)

        tmp_path = f"{config_file}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=4)
        os.replace(tmp_path, config_file)
    except Exception as e:
        _LOGGER.error("Error saving config (%s)", type(e).__name__)


# Global state storage (for cross-module state)
_global_state: Dict[str, Any] = {}


def _get_global_state() -> Dict[str, Any]:
    """Get the global state dictionary."""
    return _global_state


def set_global_state(key: str, value: Any) -> None:
    """Set a global state value."""
    _global_state[key] = value


def get_global_state(key: str, default: Any = None) -> Any:
    """Get a global state value."""
    return _global_state.get(key, default)


# === Model management ===

def get_models() -> list:
    """Load models from file."""
    try:
        with open(_resolve_models_file(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_models(models: list) -> None:
    """Save models to file."""
    models_file = _resolve_models_file()
    try:
        tmp_path = f"{models_file}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(models, f, indent=2)
        os.replace(tmp_path, models_file)
    except Exception as e:
        _LOGGER.error("Error saving models (%s)", type(e).__name__)


# === Default config for startup ===

def get_default_config() -> dict:
    """Get default configuration values."""
    return {
        "password": "admin",
        "auth_token": "",
        "auth_tokens": [],
        "cf_clearance": "",
        "api_keys": [],  # Startup generates a real default key when empty.
        "usage_stats": {},
        "prune_invalid_tokens": False,
        "persist_arena_auth_cookie": True,
        "allow_missing_api_key": False,
        "camoufox_proxy_window_mode": constants.DEFAULT_CAMOUFOX_PROXY_WINDOW_MODE,
        "camoufox_fetch_window_mode": constants.DEFAULT_CAMOUFOX_FETCH_WINDOW_MODE,
        "chrome_fetch_window_mode": constants.DEFAULT_CHROME_FETCH_WINDOW_MODE,
    }
