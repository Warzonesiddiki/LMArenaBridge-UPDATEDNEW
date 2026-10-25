import unittest
from unittest.mock import patch

import httpx

from tests._stream_test_utils import BaseBridgeTest


class TestApiContracts(BaseBridgeTest):
    def _client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.main.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    def test_estimate_tokens_is_bounded_approximation(self) -> None:
        self.assertEqual(self.main._estimate_tokens(None), 0)
        self.assertEqual(self.main._estimate_tokens(""), 0)
        self.assertEqual(self.main._estimate_tokens("abcd"), 1)
        self.assertEqual(self.main._estimate_tokens("abcde"), 2)
        self.assertEqual(self.main._estimate_tokens("a" * 40), 10)

    async def test_health_is_503_until_models_and_upstream_readiness_exist(self) -> None:
        async with self._client() as client:
            response = await client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["checks"]["models_loaded"])
        self.assertTrue(body["checks"]["auth_tokens_configured"])

    async def test_health_is_200_when_required_checks_pass(self) -> None:
        self.setup_config({"cf_clearance": "present"})
        with patch.object(self.main, "get_models", return_value=[{"publicName": "m"}]):
            async with self._client() as client:
                response = await client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["checks"]["model_count"], 1)
        self.assertTrue(body["checks"]["auth_tokens_configured"])

    async def test_health_is_503_without_any_upstream_auth(self) -> None:
        self.setup_config(
            {
                "cf_clearance": "present",
                "auth_token": "",
                "auth_tokens": [],
                "browser_cookies": {},
            }
        )
        with patch.object(self.main, "get_models", return_value=[{"publicName": "m"}]):
            async with self._client() as client:
                response = await client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["auth_tokens_configured"])

    async def test_openai_http_errors_use_openai_error_shape(self) -> None:
        async with self._client() as client:
            missing_key = await client.get("/api/v1/models")
            unknown_model = await client.post(
                "/api/v1/chat/completions",
                headers={"Authorization": "Bearer test-key"},
                json={"model": "missing", "messages": [{"role": "user", "content": "hi"}]},
            )

        self.assertEqual(missing_key.status_code, 401)
        self.assertEqual(missing_key.json()["error"]["type"], "authentication_error")
        self.assertIn("Missing API Key", missing_key.json()["error"]["message"])

        self.assertEqual(unknown_model.status_code, 404)
        self.assertEqual(unknown_model.json()["error"]["type"], "not_found_error")
        self.assertIn("not found", unknown_model.json()["error"]["message"].lower())

    async def test_non_api_http_errors_keep_detail_shape(self) -> None:
        async with self._client() as client:
            response = await client.get("/definitely-not-a-route")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not Found"})

    async def test_anthropic_validation_error_uses_anthropic_shape(self) -> None:
        async with self._client() as client:
            response = await client.post(
                "/api/v1/messages",
                headers={"Authorization": "Bearer test-key"},
                json={"model": "m", "messages": []},
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["type"], "error")
        self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.assertIn("max_tokens", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
