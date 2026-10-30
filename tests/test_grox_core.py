import unittest
from datetime import datetime, timezone

from grox_core import (
    classify_project_type,
    contains_secret_like_value,
    extract_budget,
    has_budget_information,
    is_infrastructure_qa_failure,
    is_retryable_deploy_status,
    is_retryable_status,
    retry_delay,
    sanitize_sensitive_text,
    validate_generated_files,
)


class ProjectRoutingTests(unittest.TestCase):
    def test_discord_bot_with_website_context_is_ambiguous_not_website(self):
        self.assertEqual(classify_project_type("Discord bot для сайту"), "mixed")

    def test_discord_bot_is_classified_as_bot(self):
        self.assertEqual(classify_project_type("Створи Discord-бота"), "discord_bot")

    def test_website_is_classified_as_website(self):
        self.assertEqual(classify_project_type("Потрібен адаптивний сайт"), "website")

    def test_unrecognized_task_is_not_silently_classified(self):
        self.assertEqual(classify_project_type("Потрібен аналітичний скрипт"), "unsupported")


class BudgetTests(unittest.TestCase):
    def test_recognizes_common_currencies(self):
        self.assertEqual(extract_budget("Бюджет 5 000 грн"), "5 000 грн")
        self.assertEqual(extract_budget("budget is $250"), "$250")
        self.assertTrue(has_budget_information("Поки не визначився з бюджетом"))

    def test_missing_budget_is_detected(self):
        self.assertFalse(has_budget_information("Зроби бота для Discord"))


class RetryAndSecretsTests(unittest.TestCase):
    def test_retry_after_seconds_and_capped_backoff(self):
        self.assertEqual(retry_delay("7", 2), 7.0)
        self.assertEqual(retry_delay(None, 8, maximum=60), 60)

    def test_retry_after_http_date(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(retry_delay("Thu, 01 Jan 2026 00:00:10 GMT", 1, now=now), 10.0)

    def test_only_transient_http_statuses_are_retried(self):
        self.assertFalse(is_retryable_status(401))
        self.assertFalse(is_retryable_status(403))
        self.assertTrue(is_retryable_status(429))
        self.assertTrue(is_retryable_status(503))

    def test_deployment_post_only_retries_explicit_rate_limit(self):
        self.assertTrue(is_retryable_deploy_status(429))
        self.assertFalse(is_retryable_deploy_status(401))
        self.assertFalse(is_retryable_deploy_status(503))

    def test_infrastructure_qa_failure_does_not_look_like_code_failure(self):
        self.assertTrue(is_infrastructure_qa_failure({"category": "infrastructure"}))
        self.assertFalse(is_infrastructure_qa_failure({"category": "code"}))

    def test_secret_detection_and_redaction(self):
        token = "gsk_" + "A" * 24
        self.assertTrue(contains_secret_like_value(token))
        self.assertNotIn(token, sanitize_sensitive_text("provider returned " + token))

    def test_generated_files_require_syntax_and_no_inline_key(self):
        self.assertEqual(validate_generated_files({"main.py": "def run():\n    return True", "requirements.txt": "discord.py"}), [])
        self.assertIn("syntax_error:main.py:1", validate_generated_files({"main.py": "def broken(:", "requirements.txt": "x"}))
        secret = "sk-" + "B" * 24
        self.assertIn("possible_secret:main.py", validate_generated_files({"main.py": "KEY = '" + secret + "'", "requirements.txt": "x"}))


    def test_discord_project_requires_readiness_health_gate(self):
        files = {
            "main.py": (
                'app.router.add_get("/healthz", live)\n'
                'app.router.add_get("/readyz", ready)\n'
                "if not bot.is_ready():\n    return 503\n"
            ),
            "requirements.txt": "discord.py",
        }
        self.assertEqual(
            validate_generated_files(files, expected_platform="Discord"),
            [],
        )
        files["main.py"] = "return 200"
        self.assertIn(
            "missing_discord_readiness_check",
            validate_generated_files(files, expected_platform="Discord"),
        )


if __name__ == "__main__":
    unittest.main()
