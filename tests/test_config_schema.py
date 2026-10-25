import copy
import json
import tempfile
import unittest
from pathlib import Path

from src import config as config_module
from src import main as main_module


class TestConfigSchema(unittest.TestCase):
    def test_normalize_config_preserves_unknown_keys_and_coerces_known_fields(self) -> None:
        raw = {
            "password": "",
            "auth_token": 12345,
            "auth_tokens": " token-one ",
            "cf_clearance": None,
            "browser_cookies": {
                "arena-auth-prod-v1": "cookie-token",
                "bad": {"nested": True},
            },
            "api_keys": [
                {"name": 42, "key": " bridge-key ", "rpm": "25", "created": "1700000000", "extra": "keep"},
                {"name": "blank", "key": ""},
                "not-a-dict",
            ],
            "usage_stats": {"model-a": "3", "model-b": -2, "model-c": "bad"},
            "prune_invalid_tokens": "yes",
            "persist_arena_auth_cookie": "off",
            "allow_missing_api_key": "sometimes",
            "camoufox_proxy_window_mode": "VISIBLE",
            "camoufox_fetch_window_mode": "sideways",
            "chrome_fetch_window_mode": 7,
            "stream_total_timeout_seconds": "99999",
            "userscript_proxy_poll_timeout_seconds": 0,
            "userscript_proxy_secret": 99,
            "custom_future_setting": {"nested": [1, 2, 3]},
        }

        normalized, warnings = config_module.normalize_config(raw)

        self.assertEqual(normalized["custom_future_setting"], {"nested": [1, 2, 3]})
        self.assertEqual(normalized["password"], "admin")
        self.assertEqual(normalized["auth_token"], "")
        self.assertEqual(normalized["auth_tokens"], ["token-one"])
        self.assertEqual(normalized["cf_clearance"], "")
        self.assertEqual(normalized["browser_cookies"], {"arena-auth-prod-v1": "cookie-token"})
        self.assertEqual(
            normalized["api_keys"],
            [
                {
                    "name": "Unnamed Key",
                    "key": "bridge-key",
                    "rpm": 25,
                    "created": 1700000000,
                    "extra": "keep",
                }
            ],
        )
        self.assertEqual(normalized["usage_stats"], {"model-a": 3, "model-b": 0})
        self.assertIs(normalized["prune_invalid_tokens"], True)
        self.assertIs(normalized["persist_arena_auth_cookie"], False)
        self.assertIs(normalized["allow_missing_api_key"], False)
        self.assertEqual(normalized["camoufox_proxy_window_mode"], "visible")
        self.assertEqual(normalized["camoufox_fetch_window_mode"], "hide")
        self.assertEqual(normalized["chrome_fetch_window_mode"], "hide")
        self.assertEqual(normalized["stream_total_timeout_seconds"], 3600.0)
        self.assertEqual(normalized["userscript_proxy_poll_timeout_seconds"], 1)
        self.assertNotIn("userscript_proxy_secret", normalized)
        self.assertTrue(warnings)
        self.assertIn("auth_tokens", " ".join(warnings))
        self.assertNotIn("token-one", " ".join(warnings))
        self.assertNotIn("bridge-key", " ".join(warnings))

    def test_normalize_config_rejects_non_object_top_level(self) -> None:
        normalized, warnings = config_module.normalize_config(["not", "an", "object"])

        self.assertIsInstance(normalized, dict)
        self.assertEqual(normalized["auth_tokens"], [])
        self.assertEqual(normalized["api_keys"], [])
        self.assertTrue(any("top-level" in warning for warning in warnings))

    def test_validate_config_is_non_mutating_and_value_free(self) -> None:
        raw = {"auth_tokens": "secret-token", "api_keys": [{"key": ""}]}
        original = copy.deepcopy(raw)

        warnings = config_module.validate_config(raw)

        self.assertEqual(raw, original)
        self.assertTrue(warnings)
        self.assertNotIn("secret-token", " ".join(warnings))

    def test_get_and_save_config_round_trip_through_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({"api_keys": [{"key": "saved-key", "rpm": "bad"}], "future": 1}),
                encoding="utf-8",
            )
            original_path = config_module.get_config_file()
            try:
                config_module.set_config_file(str(config_path))
                loaded = config_module.get_config()
                loaded["stream_total_timeout_seconds"] = "30"
                config_module.save_config(loaded, preserve_auth_tokens=False)
                saved = json.loads(config_path.read_text(encoding="utf-8"))
            finally:
                config_module.set_config_file(original_path)

        self.assertEqual(saved["future"], 1)
        self.assertEqual(saved["api_keys"][0]["key"], "saved-key")
        self.assertEqual(saved["api_keys"][0]["rpm"], 60)
        self.assertEqual(saved["stream_total_timeout_seconds"], 30.0)

    def test_main_get_and_save_config_also_use_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
            original_path = main_module.CONFIG_FILE
            try:
                main_module.CONFIG_FILE = str(config_path)
                loaded = main_module.get_config()
                self.assertIsInstance(loaded, dict)
                loaded["api_keys"] = [{"key": "main-key", "rpm": "bad"}]
                main_module.save_config(loaded, preserve_auth_tokens=False)
                saved = json.loads(config_path.read_text(encoding="utf-8"))
            finally:
                main_module.CONFIG_FILE = original_path

        self.assertEqual(saved["api_keys"][0]["key"], "main-key")
        self.assertEqual(saved["api_keys"][0]["rpm"], 60)


if __name__ == "__main__":
    unittest.main()
