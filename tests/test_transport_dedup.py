import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import transport


class TestTransportDedupHelpers(unittest.TestCase):
    def test_browser_fetch_order_reflects_preference(self) -> None:
        self.assertEqual(
            [spec.name for spec in transport.browser_fetch_order(prefer_chrome=True)],
            ["chrome", "camoufox"],
        )
        self.assertEqual(
            [spec.name for spec in transport.browser_fetch_order(prefer_chrome=False)],
            ["camoufox", "chrome"],
        )

    def test_resolve_browser_fetch_outer_timeout_uses_spec_bounds(self) -> None:
        chrome = transport.BROWSER_FETCH_TRANSPORT_SPECS["chrome"]
        camoufox = transport.BROWSER_FETCH_TRANSPORT_SPECS["camoufox"]

        self.assertEqual(transport.resolve_browser_fetch_outer_timeout({}, chrome), 120.0)
        self.assertEqual(transport.resolve_browser_fetch_outer_timeout({}, camoufox), 180.0)
        self.assertEqual(
            transport.resolve_browser_fetch_outer_timeout(
                {"chrome_fetch_outer_timeout_seconds": "999"}, chrome
            ),
            300.0,
        )
        self.assertEqual(
            transport.resolve_browser_fetch_outer_timeout(
                {"camoufox_fetch_outer_timeout_seconds": "bad"}, camoufox
            ),
            180.0,
        )

    def test_select_browser_fetch_auth_token_prefers_valid_ephemeral_only_when_needed(self) -> None:
        fake_main = SimpleNamespace(
            is_probably_valid_arena_auth_token=lambda token: str(token).startswith("valid-"),
            is_arena_auth_token_expired=lambda token, skew_seconds=0: str(token).endswith("-expired"),
        )
        with patch.object(transport, "_m", return_value=fake_main):
            self.assertEqual(
                transport.select_browser_fetch_auth_token("valid-current", "valid-ephemeral"),
                "valid-current",
            )
            self.assertEqual(
                transport.select_browser_fetch_auth_token("valid-current-expired", "valid-ephemeral"),
                "valid-ephemeral",
            )
            self.assertEqual(
                transport.select_browser_fetch_auth_token("", "valid-ephemeral"),
                "valid-ephemeral",
            )
            self.assertEqual(
                transport.select_browser_fetch_auth_token("placeholder", "also-placeholder"),
                "placeholder",
            )


if __name__ == "__main__":
    unittest.main()
