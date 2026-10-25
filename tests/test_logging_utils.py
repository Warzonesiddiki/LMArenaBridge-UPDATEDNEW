import io
import json
import logging
import os
import sys
import unittest
from unittest.mock import patch

from src import logging_utils
from src import main as main_module


class TestLoggingUtils(unittest.TestCase):
    def tearDown(self) -> None:
        # Restore a plain text handler for the rest of the suite.
        logging_utils.configure_logging(fmt="text", stream=sys.stdout, force=True)

    def test_json_log_event_is_structured_and_redacted(self) -> None:
        stream = io.StringIO()
        logger = logging_utils.configure_logging(
            level="DEBUG", fmt="json", stream=stream, force=True
        )

        logging_utils.log_event(
            logger,
            logging.INFO,
            "unit_test",
            "Authorization: Bearer super-secret-token password=hunter2",
            api_key="sk-lmab-secret-value",
            nested={"refresh_token": "refresh-secret", "model": "fake-model"},
            count=3,
        )

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["event"], "unit_test")
        self.assertEqual(payload["logger"], "lmarenabridge")
        self.assertIn("ts", payload)
        self.assertEqual(payload["fields"]["nested"]["model"], "fake-model")
        self.assertEqual(payload["fields"]["count"], 3)
        rendered = stream.getvalue()
        self.assertNotIn("super-secret-token", rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("sk-lmab-secret-value", rendered)
        self.assertNotIn("refresh-secret", rendered)
        self.assertIn("<redacted", rendered)

    def test_text_redaction_covers_jwts_and_signed_url_parameters(self) -> None:
        rendered = logging_utils.redact_text(
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature "
            "url https://example.test/file?Signature=abc123&ok=1"
        )

        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", rendered)
        self.assertNotIn("Signature=abc123", rendered)
        self.assertIn("ok=1", rendered)

    def test_configure_logging_uses_env_and_rejects_invalid_level(self) -> None:
        stream = io.StringIO()
        with patch.dict(
            os.environ,
            {"LMAB_LOG_FORMAT": "json", "LMAB_LOG_LEVEL": "not-a-level"},
            clear=False,
        ):
            logger = logging_utils.configure_logging(stream=stream, force=True)

        self.assertEqual(logger.name, "lmarenabridge")
        self.assertTrue(logger.handlers)
        self.assertIsInstance(logger.handlers[0].formatter, logging_utils.JsonFormatter)
        self.assertIn(logger.level, (logging.DEBUG, logging.INFO))

    def test_main_debug_print_honors_debug_flag_and_uses_json_logger(self) -> None:
        stream = io.StringIO()
        logging_utils.configure_logging(level="DEBUG", fmt="json", stream=stream, force=True)
        original_debug = main_module.DEBUG
        try:
            main_module.DEBUG = False
            main_module.debug_print("should not appear")
            self.assertEqual(stream.getvalue(), "")

            main_module.DEBUG = True
            main_module.debug_print("hello", "Bearer hidden-token")
        finally:
            main_module.DEBUG = original_debug

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["level"], "DEBUG")
        self.assertIn("hello", payload["message"])
        self.assertNotIn("hidden-token", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
