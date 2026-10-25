import json
import os
import tempfile
import unittest
from pathlib import Path

from src import doctor


class TestDoctor(unittest.TestCase):
    def test_python_version_gate(self) -> None:
        self.assertEqual(doctor.check_python((3, 11, 2)).status, doctor.PASS)
        self.assertEqual(doctor.check_python((3, 9, 9)).status, doctor.FAIL)

    def test_dependencies_flag_missing_and_incompatible_playwright(self) -> None:
        versions = {
            "fastapi": "0.162.0",
            "uvicorn": "0.54.0",
            "camoufox": "0.5.8",
            "playwright": "1.63.0",
            "httpx": "0.28.1",
            "cloudscraper": "1.2.71",
            "python-multipart": "0.0.32",
            # requests deliberately missing
        }
        results = doctor.check_dependencies(
            version_getter=versions.get,
            import_error_getter=lambda _module: None,
        )
        by_name = {result.name: result for result in results}

        self.assertEqual(by_name["package:playwright"].status, doctor.FAIL)
        self.assertIn("playwright<1.63", by_name["package:playwright"].remedy)
        self.assertEqual(by_name["package:requests"].status, doctor.FAIL)
        self.assertEqual(by_name["package:fastapi"].status, doctor.PASS)

    def test_camoufox_browser_scan_is_read_only_and_finds_nested_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = doctor.check_camoufox_browser(root, "camoufox-bin")
            self.assertEqual(missing.status, doctor.FAIL)
            self.assertIn("python -m camoufox fetch", missing.remedy)

            binary = root / "browsers" / "official" / "camoufox-bin"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(binary.stat().st_mode | 0o111)

            found = doctor.check_camoufox_browser(root, "camoufox-bin")
            self.assertEqual(found.status, doctor.PASS)
            self.assertIn(str(binary), found.detail)

    def test_local_state_reports_counts_without_leaking_secrets(self) -> None:
        secret_token = "arena-secret-token-value"
        secret_key = "bridge-secret-key-value"
        secret_clearance = "cf-clearance-secret-value"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            models_path = root / "models.json"
            config_path.write_text(
                json.dumps(
                    {
                        "password": "not-admin",
                        "auth_tokens": [secret_token],
                        "cf_clearance": secret_clearance,
                        "api_keys": [{"name": "Test", "key": secret_key}],
                        "allow_missing_api_key": False,
                    }
                ),
                encoding="utf-8",
            )
            models_path.write_text(
                json.dumps([{"publicName": "model-a"}, {"publicName": "model-b"}]),
                encoding="utf-8",
            )

            results = doctor.check_local_state(config_path, models_path)
            rendered = doctor.format_results(results)
            by_name = {result.name: result for result in results}

            self.assertEqual(by_name["upstream-auth"].status, doctor.PASS)
            self.assertEqual(by_name["bridge-api-keys"].status, doctor.PASS)
            self.assertEqual(by_name["models-file"].status, doctor.PASS)
            self.assertIn("2 model record(s)", by_name["models-file"].detail)
            self.assertNotIn(secret_token, rendered)
            self.assertNotIn(secret_key, rendered)
            self.assertNotIn(secret_clearance, rendered)
            self.assertNotIn("not-admin", rendered)

    def test_local_state_warns_for_retained_insecure_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            models_path = root / "models.json"
            config_path.write_text(
                json.dumps(
                    {
                        "password": "admin",
                        "api_keys": [{"name": "Test", "key": "x"}],
                        "allow_missing_api_key": True,
                    }
                ),
                encoding="utf-8",
            )
            models_path.write_text("[]", encoding="utf-8")

            results = doctor.check_local_state(config_path, models_path)
            by_name = {result.name: result for result in results}

            self.assertEqual(by_name["bridge-api-keys"].status, doctor.WARN)
            self.assertEqual(by_name["dashboard-password"].status, doctor.WARN)
            self.assertEqual(by_name["upstream-auth"].status, doctor.WARN)


if __name__ == "__main__":
    unittest.main()
