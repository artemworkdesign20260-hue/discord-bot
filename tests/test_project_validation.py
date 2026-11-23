import os
import unittest
from unittest.mock import AsyncMock, patch

# main.py validates its required configuration at import time. These dummy
# values are used only by the unit-test process; no network connection starts.
os.environ.setdefault("DISCORD_TOKEN", "unit-test-discord-token")
os.environ.setdefault("GROQ_API_KEY", "unit-test-groq-key")
os.environ.setdefault("CLIENT_CHANNEL_ID", "123456789")
os.environ.setdefault("GROX_OWNER_ID", "123456789")
os.environ.setdefault(
    "GROX_DATABASE_URL",
    "postgresql://unit_test:unit_test@127.0.0.1:5432/unit_test",
)

from main import contains_credential_request, continue_truncated_groq_output, detect_bot_platform, is_order_message, is_revision_request, is_website_project, select_render_owner_id, validate_generated_project


class GeneratedProjectValidationTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "main.py": "print('hello')\n",
            "requirements.txt": "",
            "README.md": "Project setup and environment variables.\n",
        }

    def test_accepts_simple_valid_project(self):
        self.assertEqual(
            validate_generated_project(self.files),
            [],
        )

    def test_checks_syntax_of_secondary_python_files(self):
        self.files["helper.py"] = "def broken_function(:\n    pass\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("helper.py" in error and "Синтаксична помилка" in error for error in errors))

    def test_detects_aliased_os_system_import(self):
        self.files["main.py"] = "from os import system as run_command\nrun_command('whoami')\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("небезпечної функції run_command" in error for error in errors))

    def test_detects_aliased_os_popen(self):
        self.files["helper.py"] = "import os as operating_system\noperating_system.popen('id')\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("os.popen" in error for error in errors))

    def test_detects_subprocess_in_secondary_file(self):
        self.files["worker.py"] = "from subprocess import run\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("worker.py" in error and "subprocess" in error for error in errors))

    def test_detects_internal_secret_access_through_environ_get(self):
        self.files["helper.py"] = "import os as operating_system\noperating_system.environ.get('GITHUB_TOKEN')\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("GITHUB_TOKEN" in error for error in errors))

    def test_allows_bot2_database_url(self):
        self.files["main.py"] = "import os\nDATABASE_URL = os.getenv('DATABASE_URL')\n"
        errors = validate_generated_project(self.files)
        self.assertFalse(any("DATABASE_URL" in error for error in errors))

    def test_does_not_flag_comments_or_string_literals(self):
        self.files["main.py"] = (
            "# subprocess and os.system are mentioned in this comment\n"
            "message = 'os.system(\\'not executed\\')'\n"
        )
        errors = validate_generated_project(self.files)
        self.assertFalse(any("subprocess" in error or "os.system" in error for error in errors))


    def test_checks_known_external_import_dependencies(self):
        self.files["main.py"] = "import aiohttp\\n"
        errors = validate_generated_project(self.files)
        self.assertTrue(any("aiohttp" in error and "requirements.txt" in error for error in errors))

    def test_accepts_declared_external_import_dependency(self):
        self.files["main.py"] = "import aiohttp\\n"
        self.files["requirements.txt"] = "aiohttp>=3.9\\n"
        errors = validate_generated_project(self.files)
        self.assertFalse(any("aiohttp" in error for error in errors))


class RenderOwnerSelectionTests(unittest.TestCase):
    def test_selects_the_only_available_owner(self):
        self.assertEqual(
            select_render_owner_id([{"id": "workspace-1"}]),
            "workspace-1",
        )

    def test_requires_explicit_owner_when_multiple_are_available(self):
        with self.assertRaises(RuntimeError):
            select_render_owner_id([{"id": "personal"}, {"id": "workspace"}])

    def test_selects_configured_nested_owner(self):
        self.assertEqual(
            select_render_owner_id(
                [{"owner": {"id": "workspace-1"}}, {"id": "workspace-2"}],
                "workspace-1",
            ),
            "workspace-1",
        )


class ProjectRoutingTests(unittest.TestCase):
    def test_website_with_discord_integration_routes_to_website(self):
        self.assertTrue(
            is_website_project("Створи сайт компанії з інтеграцією Discord.")
        )

    def test_discord_bot_with_website_dashboard_routes_to_bot(self):
        self.assertFalse(
            is_website_project("Створи Discord bot з вебпанеллю керування.")
        )

    def test_discord_bot_with_telegram_notifications_stays_discord(self):
        self.assertEqual(
            detect_bot_platform("Створи Discord bot, який надсилає сповіщення в Telegram."),
            "Discord",
        )

    def test_telegram_bot_with_discord_notifications_stays_telegram(self):
        self.assertEqual(
            detect_bot_platform("Створи Telegram bot з повідомленнями в Discord."),
            "Telegram",
        )

    def test_bot2_is_always_discord(self):
        self.assertEqual(detect_bot_platform("Bot 2 з інтеграцією Telegram"), "Discord")


class RevisionIntentTests(unittest.TestCase):
    def test_question_about_color_is_not_a_revision(self):
        self.assertFalse(is_revision_request("Який колір краще обрати для кнопки?"))

    def test_question_about_button_is_not_a_revision(self):
        self.assertFalse(is_revision_request("Поясни, для чого ця кнопка."))

    def test_explicit_button_change_is_a_revision(self):
        self.assertTrue(is_revision_request("Зроби кнопку синьою."))

    def test_broken_button_is_a_revision(self):
        self.assertTrue(is_revision_request("Кнопка не працює."))

    def test_add_section_is_a_revision(self):
        self.assertTrue(is_revision_request("Додай секцію контактів."))


class CredentialRequestDetectionTests(unittest.TestCase):
    def test_key_features_are_not_a_credential_request(self):
        self.assertFalse(contains_credential_request("Опиши ключові функції застосунку."))

    def test_api_key_is_a_credential_request(self):
        self.assertTrue(contains_credential_request("Надішліть API key для інтеграції."))

    def test_private_key_is_a_credential_request(self):
        self.assertTrue(contains_credential_request("Потрібен приватний ключ доступу."))


class GroqContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_appends_continuation(self):
        with patch(
            "main.groq_request",
            new=AsyncMock(return_value={"content": "def", "finish_reason": "stop"}),
        ):
            result, still_truncated = await continue_truncated_groq_output(
                "abc", "task context", "plain text"
            )
        self.assertEqual(result, "abcdef")
        self.assertFalse(still_truncated)

    async def test_removes_repeated_overlap(self):
        with patch(
            "main.groq_request",
            new=AsyncMock(return_value={"content": "defghi", "finish_reason": "stop"}),
        ):
            result, still_truncated = await continue_truncated_groq_output(
                "abcdef", "task context", "plain text"
            )
        self.assertEqual(result, "abcdefghi")
        self.assertFalse(still_truncated)

    async def test_reports_still_truncated_after_bounded_rounds(self):
        with patch(
            "main.groq_request",
            new=AsyncMock(return_value={"content": "more", "finish_reason": "length"}),
        ) as mocked:
            result, still_truncated = await continue_truncated_groq_output(
                "start", "task context", "plain text", max_rounds=3
            )
        self.assertEqual(mocked.await_count, 3)
        self.assertTrue(still_truncated)
        self.assertTrue(result.startswith("start"))


class Bot2ValidationTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "main.py": (
                "import discord\\n"
                "import sqlite3\\n"
                "BOT2_MIN_GUILD_MEMBERS = 500\\n"
                "async def on_message(message):\\n    pass\\n"
                "def is_moderator(member):\\n    return True\\n"
                "EU_COUNTRIES = ['DE', 'PL']\\n"
                "def search_requests():\\n    pass\\n"
                "processed_message_ids = set()\\n"
                "async def health_handler():\\n    pass\\n"
            ),
            "requirements.txt": "discord.py>=2.0\\n",
            "README.md": "Discord bot setup and Environment Variables.\\n",
        }

    def test_accepts_required_bot2_ast_signals(self):
        errors = validate_generated_project(
            self.files,
            bot2=True,
            require_discord=True,
        )
        self.assertEqual(errors, [])

    def test_comments_and_strings_cannot_fake_bot2_features(self):
        self.files["main.py"] = (
            "# import discord on_message member_count moderator approval\\n"
            "description = '500 sqlite EU search duplicate /health'\\n"
        )
        errors = validate_generated_project(
            self.files,
            bot2=True,
            require_discord=True,
        )
        self.assertGreaterEqual(len(errors), 5)


class NewOrderIntentTests(unittest.TestCase):
    def test_casual_site_mention_does_not_open_order(self):
        self.assertFalse(is_order_message("У мене є сайт і бот, вони працюють."))

    def test_broken_existing_bot_does_not_open_new_order(self):
        self.assertFalse(is_order_message("Мій bot is down."))

    def test_explicit_create_website_opens_order(self):
        self.assertTrue(is_order_message("Створи сайт для магазину."))

    def test_need_discord_bot_opens_order(self):
        self.assertTrue(is_order_message("Мені потрібен Discord bot."))

    def test_build_web_app_opens_order(self):
        self.assertTrue(is_order_message("Build a web app for my project."))


if __name__ == "__main__":
    unittest.main()
