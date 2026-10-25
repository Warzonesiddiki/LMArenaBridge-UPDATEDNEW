"""Regression tests for config/models file ownership.

Previously ``src.config`` kept its own ``_current_config_file`` and always
used ``constants.MODELS_FILE``, ignoring ``main.CONFIG_FILE`` /
``main.MODELS_FILE`` patched by tests. Code paths that saved config via the
config module therefore wrote to the real ``./config.json`` during tests
(observed live: a pytest run created a repo-root config.json containing
test data). The config module must follow ``main``'s paths.
"""

import json
import tempfile
import unittest
from pathlib import Path


class TestConfigModuleFollowsMainPaths(unittest.TestCase):
    def setUp(self) -> None:
        from src import main
        from src import config as config_module

        self.main = main
        self.config_module = config_module
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_config = main.CONFIG_FILE
        self._orig_models = main.MODELS_FILE
        self.cfg_path = Path(self._tmp.name) / "cfg.json"
        self.models_path = Path(self._tmp.name) / "models.json"
        main.CONFIG_FILE = str(self.cfg_path)
        main.MODELS_FILE = str(self.models_path)

    def tearDown(self) -> None:
        self.main.CONFIG_FILE = self._orig_config
        self.main.MODELS_FILE = self._orig_models
        self._tmp.cleanup()

    def test_save_and_get_config_use_main_config_file(self) -> None:
        self.config_module.save_config(
            {"password": "x", "api_keys": [], "auth_tokens": ["t1"]},
            preserve_auth_tokens=False,
        )
        self.assertTrue(self.cfg_path.exists())
        loaded = self.config_module.get_config()
        self.assertEqual(loaded.get("auth_tokens"), ["t1"])

    def test_save_and_get_models_use_main_models_file(self) -> None:
        models = [{"publicName": "m1", "id": "1"}]
        self.config_module.save_models(models)
        self.assertTrue(self.models_path.exists())
        self.assertEqual(json.loads(self.models_path.read_text(encoding="utf-8")), models)
        self.assertEqual(self.config_module.get_models(), models)


if __name__ == "__main__":
    unittest.main()
