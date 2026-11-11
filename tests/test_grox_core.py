import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from grox_core import (
    discord_start_retry_delay,
    extract_discord_retry_after,
    generated_code_issues,
    is_cloudflare_1015,
    run_discord_start_with_retries,
    should_retry_discord_start,
)


class DiscordStartupRetryTests(unittest.TestCase):
    class HttpError(Exception):
        def __init__(self, status, text="", headers=None):
            super().__init__("test HTTP failure")
            self.status = status
            self.text = text
            self.response = SimpleNamespace(headers=headers or {})

    def test_retries_rate_limits_server_errors_and_network_failures(self):
        self.assertTrue(should_retry_discord_start(429))
        self.assertTrue(should_retry_discord_start(503))
        self.assertTrue(should_retry_discord_start(network_error=True))
        self.assertTrue(should_retry_discord_start(rate_limited=True))

    def test_does_not_retry_permanent_auth_or_permission_errors(self):
        self.assertFalse(should_retry_discord_start(401))
        self.assertFalse(should_retry_discord_start(403))
        self.assertFalse(should_retry_discord_start(400))

    def test_detects_cloudflare_1015_even_with_non_429_status(self):
        self.assertTrue(is_cloudflare_1015(403, "<title>Error 1015: You are being rate limited</title>"))
        self.assertTrue(is_cloudflare_1015(1015, ""))
        self.assertFalse(is_cloudflare_1015(429, "You are being rate limited"))

    def test_extracts_retry_after_from_response_header_or_body(self):
        from_header = SimpleNamespace(
            response=SimpleNamespace(headers={"Retry-After": "12.5"}),
            text="",
        )
        from_body = SimpleNamespace(
            response=None,
            text='{"retry_after": 3.25}',
        )
        self.assertEqual(extract_discord_retry_after(from_header), "12.5")
        self.assertEqual(extract_discord_retry_after(from_body), 3.25)

    def test_obeys_retry_after_and_parses_http_date(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(discord_start_retry_delay("7", 1), 7.0)
        self.assertEqual(
            discord_start_retry_delay(
                "Thu, 01 Jan 2026 00:00:10 GMT",
                1,
                now=now,
            ),
            10.0,
        )

    def test_cloudflare_1015_uses_long_capped_backoff(self):
        self.assertEqual(
            discord_start_retry_delay(None, 1, cloudflare_1015_error=True),
            300.0,
        )
        self.assertEqual(
            discord_start_retry_delay(None, 99, cloudflare_1015_error=True),
            3600.0,
        )
        self.assertGreaterEqual(
            discord_start_retry_delay("30", 1, cloudflare_1015_error=True),
            300.0,
        )

    def test_start_loop_retries_429_then_returns_success(self):
        async def run_test():
            calls = 0
            delays = []

            async def start(token):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise self.HttpError(
                        429,
                        '{"retry_after": 2}',
                        {"Retry-After": "2"},
                    )
                return "connected"

            async def fake_sleep(delay):
                delays.append(delay)

            result = await run_discord_start_with_retries(
                start,
                "test-token",
                sleep=fake_sleep,
                network_error=lambda error: False,
            )
            self.assertEqual(result, "connected")
            self.assertEqual(calls, 2)
            self.assertEqual(delays, [2.0])

        asyncio.run(run_test())

    def test_start_loop_waits_long_after_cloudflare_1015(self):
        async def run_test():
            calls = 0
            delays = []
            retry_logs = []

            async def start(token):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise self.HttpError(
                        403,
                        "<title>Error 1015: Cloudflare rate limited</title>",
                    )
                return "connected"

            async def fake_sleep(delay):
                delays.append(delay)

            result = await run_discord_start_with_retries(
                start,
                "test-token",
                sleep=fake_sleep,
                network_error=lambda error: False,
                on_retry=lambda *values: retry_logs.append(values),
            )
            self.assertEqual(result, "connected")
            self.assertEqual(calls, 2)
            self.assertEqual(delays, [300.0])
            self.assertEqual(retry_logs[0][0], "cloudflare-1015")

        asyncio.run(run_test())

    def test_start_loop_does_not_retry_bad_credentials(self):
        async def run_test():
            calls = 0
            delays = []

            async def start(token):
                nonlocal calls
                calls += 1
                raise self.HttpError(401, "invalid token")

            async def fake_sleep(delay):
                delays.append(delay)

            with self.assertRaises(self.HttpError):
                await run_discord_start_with_retries(
                    start,
                    "test-token",
                    sleep=fake_sleep,
                    network_error=lambda error: False,
                )
            self.assertEqual(calls, 1)
            self.assertEqual(delays, [])

        asyncio.run(run_test())


class GeneratedOutputChecksTests(unittest.TestCase):
    def test_rejects_simulated_community_search(self):
        files = {
            "main.py": (
                "async def simulate_search():\n"
                "    return ['dummy_guild']\n"
            ),
            "requirements.txt": "discord.py",
            "README.md": "Search communities",
        }
        self.assertIn(
            "mock_or_fabricated_implementation:main.py",
            generated_code_issues(files, bot2=True),
        )

    def test_rejects_known_retired_groq_model(self):
        files = {
            "main.py": 'MODEL = "mixtral-8x7b-32768"\n',
            "requirements.txt": "httpx",
            "README.md": "Groq bot",
        }
        self.assertIn(
            "retired_groq_model:main.py",
            generated_code_issues(files),
        )

    def test_bot2_rejects_direct_message_sending(self):
        files = {
            "main.py": "await user.send(message)\n",
            "requirements.txt": "discord.py",
            "README.md": "Discord bot",
        }
        self.assertIn(
            "bot2_unsolicited_direct_message_path:main.py",
            generated_code_issues(files, bot2=True),
        )

    def test_regular_discord_bot_can_have_a_dm_feature(self):
        files = {
            "main.py": "await user.send(message)\n",
            "requirements.txt": "discord.py",
            "README.md": "Discord bot",
        }
        self.assertEqual(generated_code_issues(files, bot2=False), [])


if __name__ == "__main__":
    unittest.main()
