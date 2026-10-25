import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from tests._stream_test_utils import BaseBridgeTest


class _FakeRequest:
    def __init__(self, session_id: str | None = None) -> None:
        self.cookies = {"session_id": session_id} if session_id else {}


class TestStateBounds(BaseBridgeTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.main.dashboard_sessions.clear()
        self.main._dashboard_session_created_at.clear()
        self._orig_max_sessions = self.main.MAX_CHAT_SESSIONS_PER_KEY
        self._orig_max_messages = self.main.MAX_MESSAGES_PER_SESSION
        self._orig_dashboard_ttl = self.main.DASHBOARD_SESSION_TTL_SECONDS

    async def asyncTearDown(self) -> None:
        self.main.MAX_CHAT_SESSIONS_PER_KEY = self._orig_max_sessions
        self.main.MAX_MESSAGES_PER_SESSION = self._orig_max_messages
        self.main.DASHBOARD_SESSION_TTL_SECONDS = self._orig_dashboard_ttl
        self.main.dashboard_sessions.clear()
        self.main._dashboard_session_created_at.clear()
        await super().asyncTearDown()

    def test_conversation_id_uses_full_first_message_and_system_prompt(self) -> None:
        shared_prefix = "x" * 150
        first = self.main._conversation_id_for("key", "model", shared_prefix + " A", "system")
        second = self.main._conversation_id_for("key", "model", shared_prefix + " B", "system")
        different_system = self.main._conversation_id_for(
            "key", "model", shared_prefix + " A", "different system"
        )

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, different_system)
        self.assertEqual(first, self.main._conversation_id_for("key", "model", shared_prefix + " A", "system"))
        self.assertEqual(len(first), 64)

    def test_chat_sessions_and_messages_are_bounded(self) -> None:
        self.main.MAX_CHAT_SESSIONS_PER_KEY = 2
        self.main.MAX_MESSAGES_PER_SESSION = 3

        self.main._bounded_session_put(
            "test-key",
            "c1",
            {"conversation_id": "up-1", "messages": [{"content": str(i)} for i in range(5)]},
        )
        self.main._bounded_session_put(
            "test-key",
            "c2",
            {"conversation_id": "up-2", "messages": []},
        )
        self.assertEqual(
            [m["content"] for m in self.main.chat_sessions["test-key"]["c1"]["messages"]],
            ["2", "3", "4"],
        )

        self.main._bounded_session_put(
            "test-key",
            "c2",
            append_messages=[{"content": "new"}],
        )
        self.main._bounded_session_put(
            "test-key",
            "c3",
            {"conversation_id": "up-3", "messages": []},
        )

        self.assertEqual(set(self.main.chat_sessions["test-key"]), {"c2", "c3"})

    async def test_dashboard_session_expires_after_ttl(self) -> None:
        self.main.DASHBOARD_SESSION_TTL_SECONDS = 60
        now = time.time()
        self.main.dashboard_sessions["valid"] = "admin"
        self.main._dashboard_session_created_at["valid"] = now
        self.main.dashboard_sessions["expired"] = "admin"
        self.main._dashboard_session_created_at["expired"] = now - 120

        self.assertEqual(await self.main.get_current_session(_FakeRequest("valid")), "admin")
        self.assertIsNone(await self.main.get_current_session(_FakeRequest("expired")))
        self.assertNotIn("expired", self.main.dashboard_sessions)
        self.assertNotIn("expired", self.main._dashboard_session_created_at)

    async def test_tracked_background_tasks_can_be_cancelled_together(self) -> None:
        task = self.main._track_background_task(asyncio.create_task(asyncio.sleep(60)))
        self.assertIn(task, self.main._BACKGROUND_TASKS)

        cancelled = await self.main._cancel_background_tasks()

        self.assertEqual(cancelled, 1)
        self.assertTrue(task.done())
        self.assertNotIn(task, self.main._BACKGROUND_TASKS)

    async def test_dashboard_escapes_untrusted_names_and_masks_secrets(self) -> None:
        self.setup_config(
            {
                "api_keys": [
                    {
                        "name": "<script>alert(1)</script>",
                        "key": "x'\"><svg onload=alert(2)>",
                        "rpm": 10,
                        "created": 1700000000,
                    }
                ],
                "auth_tokens": ["<b>arena-secret-token</b>"],
                "cf_clearance": "<script>cf-secret</script>",
            }
        )
        self.main.dashboard_sessions["dashboard-session"] = "admin"
        self.main._dashboard_session_created_at["dashboard-session"] = time.time()

        malicious_model = {
            "publicName": "<img src=x onerror=alert(3)>",
            "organization": "<b>Org</b>",
            "rank": 1,
            "capabilities": {"outputCapabilities": {"text": True}},
        }
        transport = httpx.ASGITransport(app=self.main.app, raise_app_exceptions=False)
        with patch.object(self.main, "get_models", return_value=[malicious_model]):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/dashboard",
                    headers={"Cookie": "session_id=dashboard-session"},
                )

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<svg onload=alert(2)>", body)
        self.assertNotIn("<img src=x onerror=alert(3)>", body)
        self.assertIn("&lt;img src=x onerror=alert(3)&gt;", body)
        self.assertNotIn("<b>arena-secret-token</b>", body)
        self.assertNotIn("arena-secret-token", body)
        self.assertNotIn("<script>cf-secret</script>", body)
        self.assertNotIn("cf-secret", body)
        self.assertIn("sha256:", body)


if __name__ == "__main__":
    unittest.main()
