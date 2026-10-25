import unittest
from unittest.mock import AsyncMock, patch

from tests._stream_test_utils import BaseBridgeTest


class TestTransportRegressions(BaseBridgeTest):
    async def test_fetch_via_proxy_queue_legacy_timeout_cleans_main_state(self) -> None:
        """The legacy queue fallback must use main's queue state, not bare names."""
        with patch.object(
            self.main,
            "fetch_lmarena_stream_via_userscript_proxy",
            AsyncMock(return_value=None),
        ):
            result = await self.main.fetch_via_proxy_queue(
                url="https://arena.ai/nextjs-api/stream/create-evaluation",
                payload={"id": "test"},
                http_method="POST",
                timeout_seconds=1,
            )

        self.assertIsNone(result)
        self.assertEqual(self.main.proxy_pending_tasks, {})
        self.assertEqual(self.main.proxy_task_queue, [])

    def test_worker_backoff_is_bounded_and_increasing(self) -> None:
        from src.transport import _worker_backoff_seconds

        self.assertEqual(_worker_backoff_seconds(0), 5.0)
        self.assertEqual(_worker_backoff_seconds(1), 10.0)
        self.assertEqual(_worker_backoff_seconds(3), 40.0)
        self.assertEqual(_worker_backoff_seconds(100), 300.0)
        self.assertEqual(_worker_backoff_seconds(-5), 5.0)


if __name__ == "__main__":
    unittest.main()
