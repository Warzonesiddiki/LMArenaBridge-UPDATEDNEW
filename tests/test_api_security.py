import unittest

import httpx

from tests._stream_test_utils import BaseBridgeTest


class TestApiSecurity(BaseBridgeTest):
    async def _get_models(self, headers: dict | None = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/models", headers=headers or {})

    async def test_missing_api_key_rejected_by_default(self) -> None:
        response = await self._get_models()
        self.assertEqual(response.status_code, 401)

    async def test_wrong_bearer_api_key_rejected(self) -> None:
        response = await self._get_models({"Authorization": "Bearer wrong-key"})
        self.assertEqual(response.status_code, 401)

    async def test_valid_bearer_and_x_api_key_accepted(self) -> None:
        bearer = await self._get_models({"Authorization": "Bearer test-key"})
        header = await self._get_models({"x-api-key": "test-key"})
        self.assertEqual(bearer.status_code, 200)
        self.assertEqual(header.status_code, 200)

    async def test_missing_api_key_can_be_explicitly_allowed_for_legacy_clients(self) -> None:
        self.setup_config({"allow_missing_api_key": True})
        response = await self._get_models()
        self.assertEqual(response.status_code, 200)

    async def test_no_configured_api_keys_keeps_initial_setup_anonymous(self) -> None:
        self.setup_config({"api_keys": []})
        response = await self._get_models()
        self.assertEqual(response.status_code, 200)

    def test_secret_fingerprint_and_payload_summary_do_not_expose_secrets(self) -> None:
        secret = "base64-super-secret-token-value"
        fingerprint = self.main._secret_fingerprint(secret)
        self.assertNotIn(secret, fingerprint)
        self.assertNotIn(secret[:20], fingerprint)
        self.assertIn("sha256:", fingerprint)

        summary = self.main._payload_log_summary(
            {
                "id": "session-1",
                "mode": "direct",
                "modelAId": "model-1",
                "modality": "text",
                "userMessage": {
                    "content": "private prompt text",
                    "experimental_attachments": [{"name": "a.png"}],
                },
                "recaptchaV3Token": secret,
            }
        )
        rendered = str(summary)
        self.assertNotIn("private prompt text", rendered)
        self.assertNotIn(secret, rendered)
        self.assertEqual(summary["content_chars"], len("private prompt text"))
        self.assertTrue(summary["has_recaptcha_v3"])


if __name__ == "__main__":
    unittest.main()
