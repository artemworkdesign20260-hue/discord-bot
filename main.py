import asyncio
import os
import re
import uuid
import traceback

import aiohttp
from aiohttp import web

import discord
from discord.ext import commands

from google import genai
from google.genai import types


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")

CLIENT_CHANNEL_ID = os.getenv("CLIENT_CHANNEL_ID")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.7-flash"
)

CLIENT_TIMEOUT = int(
    os.getenv("CLIENT_TIMEOUT", "600")
)

PORT = int(
    os.getenv("PORT", "10000")
)


# ============================================================
# CHECK ENVIRONMENT
# ============================================================

required_variables = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "VERCEL_TOKEN": VERCEL_TOKEN,
    "CLIENT_CHANNEL_ID": CLIENT_CHANNEL_ID,
}

missing_variables = [
    name
    for name, value in required_variables.items()
    if not value
]

if missing_variables:
    raise RuntimeError(
        "Відсутні Environment Variables: "
        + ", ".join(missing_variables)
    )


try:
    CLIENT_CHANNEL_ID = int(CLIENT_CHANNEL_ID)

except ValueError:
    raise RuntimeError(
        "CLIENT_CHANNEL_ID повинен бути числом."
    )


# ============================================================
# GEMINI
# ============================================================

try:

    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    print(
        f"[GEMINI] Client initialized. "
        f"Model: {GEMINI_MODEL}"
    )

except Exception as error:

    print("========== GEMINI CLIENT ERROR ==========")
    print(
        f"Type: {type(error).__name__}"
    )
    print(
        f"Message: {error!r}"
    )
    traceback.print_exc()
    print("=========================================")

    raise


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()

intents.message_content = True
intents.messages = True
intents.guilds = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# ORDER DETECTION
# ============================================================

KEYWORDS = [
    "bot",
    "бот",
    "bots",
    "бота",
    "ботом",

    "site",
    "сайт",
    "website",
    "web",
    "веб",

    "app",
    "апка",
    "додаток",

    "script",
    "скрипт",

    "development",
    "розробка",

    "landing",
    "лендинг",

    "discord bot",
    "discord бот",
]


BUDGET_PATTERN = re.compile(
    r"""
    (?:
        \$\s*(\d[\d\s,\.]*)
        |
        (\d[\d\s,\.]*)\s*\$
        |
        (\d[\d\s,\.]*)\s*(?:usd|dollars?|долар(?:ів|и)?)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


MINIMUM_BUDGET = 200


def extract_budget(text: str):

    match = BUDGET_PATTERN.search(text)

    if not match:
        return None

    for group in match.groups():

        if group:

            try:

                cleaned = (
                    group
                    .replace(" ", "")
                    .replace(",", "")
                    .replace(".", "")
                )

                return int(cleaned)

            except ValueError:

                return None

    return None


def contains_service_keyword(
    text: str
) -> bool:

    text = text.lower()

    return any(
        keyword in text
        for keyword in KEYWORDS
    )


def is_order_message(
    text: str
) -> bool:

    if not contains_service_keyword(text):
        return False

    budget = extract_budget(text)

    if budget is None:
        return False

    return budget >= MINIMUM_BUDGET


# ============================================================
# ACTIVE ORDERS
# ============================================================

active_orders = set()


# ============================================================
# GEMINI WEBSITE GENERATION
# ============================================================

async def generate_site_code(
    client_task: str
) -> str:

    print(
        "========================================"
    )

    print(
        "[GEMINI] Починаю генерацію сайту..."
    )

    print(
        f"[GEMINI] Model: {GEMINI_MODEL}"
    )

    print(
        f"[GEMINI] ТЗ length: "
        f"{len(client_task)} characters"
    )

    print(
        "========================================"
    )


    prompt = f"""
Ти — професійний веб-розробник системи Grox.

Твоє завдання — створити повністю готовий до запуску
односторінковий вебсайт за технічним завданням клієнта.

ВАЖЛИВІ ВИМОГИ:

1. Поверни ТІЛЬКИ HTML-код.
2. Не використовуй Markdown.
3. Не використовуй ```html.
4. CSS повинен бути всередині HTML.
5. JavaScript повинен бути всередині HTML.
6. Сайт повинен бути адаптивним для телефону і ПК.
7. Дизайн повинен виглядати професійно.
8. Використовуй сучасний UI/UX.
9. Якщо клієнт не вказав кольори — вибери професійну кольорову схему.
10. Не додавай пояснення перед або після HTML.
11. Код повинен бути одним повним файлом index.html.
12. Не залишай незаповнених TODO.
13. Не використовуй зовнішні файли CSS або JS.
14. Сайт повинен працювати одразу після відкриття index.html.
15. Переконайся, що HTML має правильну структуру DOCTYPE, html, head та body.

Технічне завдання клієнта:

{client_task}
"""


    try:

        print(
            "[GEMINI] Відправляю запит до Gemini..."
        )

        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=30000,
            )
        )

        print(
            "[GEMINI] Запит успішно завершений."
        )


    except Exception as error:

        print("")
        print("========================================")
        print("🔥🔥🔥 GEMINI EXCEPTION 🔥🔥🔥")
        print("========================================")

        print(
            f"ERROR TYPE: {type(error).__name__}"
        )

        print(
            f"ERROR MESSAGE: {str(error)}"
        )

        print(
            f"ERROR REPR: {repr(error)}"
        )

        print("")
        print("FULL TRACEBACK:")

        traceback.print_exc()

        print("")
        print("========================================")
        print("END GEMINI EXCEPTION")
        print("========================================")
        print("")

        raise


    print(
        "[GEMINI] Відповідь отримана."
    )


    # ========================================================
    # CHECK RESPONSE OBJECT
    # ========================================================

    if response is None:

        print(
            "[GEMINI ERROR] "
            "Response object is None."
        )

        raise RuntimeError(
            "Gemini повернув порожній response."
        )


    # ========================================================
    # RESPONSE TEXT
    # ========================================================

    try:

        response_text = response.text

    except Exception as error:

        print(
            "[GEMINI RESPONSE TEXT ERROR]"
        )

        print(
            f"Type: {type(error).__name__}"
        )

        print(
            f"Message: {error!r}"
        )

        traceback.print_exc()

        raise


    if not response_text:

        print(
            "[GEMINI ERROR] "
            "Модель повернула порожню відповідь."
        )

        print(
            "[GEMINI RESPONSE OBJECT]"
        )

        print(
            repr(response)
        )

        # ----------------------------------------------------
        # TRY TO SHOW CANDIDATES
        # ----------------------------------------------------

        try:

            print(
                "[GEMINI CANDIDATES]"
            )

            print(
                repr(response.candidates)
            )

        except Exception as error:

            print(
                "[GEMINI] "
                "Не вдалося прочитати candidates:",
                repr(error)
            )

        raise RuntimeError(
            "Gemini не повернув код."
        )


    html = response_text.strip()


    print(
        f"[GEMINI] Response length: "
        f"{len(html)} characters."
    )


    # ========================================================
    # CLEAN MARKDOWN
    # ========================================================

    html = re.sub(
        r"^```html\s*",
        "",
        html,
        flags=re.IGNORECASE
    )

    html = re.sub(
        r"^```\s*",
        "",
        html
    )

    html = re.sub(
        r"\s*```$",
        "",
        html
    )

    html = html.strip()


    # ========================================================
    # BASIC HTML CHECK
    # ========================================================

    if "<html" not in html.lower():

        print(
            "[GEMINI ERROR] "
            "Відповідь не схожа на HTML."
        )

        print(
            "[GEMINI RESPONSE PREVIEW]"
        )

        print(
            html[:2000]
        )

        raise RuntimeError(
            "Gemini повернув некоректний HTML."
        )


    if "<body" not in html.lower():

        print(
            "[GEMINI ERROR] "
            "У HTML немає body."
        )

        print(
            "[GEMINI RESPONSE PREVIEW]"
        )

        print(
            html[:2000]
        )

        raise RuntimeError(
            "Gemini повернув неповний HTML."
        )


    if "<head" not in html.lower():

        print(
            "[GEMINI ERROR] "
            "У HTML немає head."
        )

        raise RuntimeError(
            "Gemini повернув неповний HTML."
        )


    print(
        f"[GEMINI] HTML готовий. "
        f"Довжина: {len(html)} символів."
    )

    print(
        "[GEMINI] Генерація успішна."
    )

    return html


# ============================================================
# VERCEL DEPLOY
# ============================================================

async def deploy_to_vercel(
    project_name: str,
    html_content: str
):

    url = (
        "https://api.vercel.com/v13/deployments"
    )

    headers = {
        "Authorization":
            f"Bearer {VERCEL_TOKEN}",

        "Content-Type":
            "application/json",
    }

    payload = {

        "name":
            project_name,

        "files": [

            {
                "file":
                    "index.html",

                "data":
                    html_content,
            }

        ],

        "projectSettings": {

            "framework":
                None
        }
    }

    timeout = aiohttp.ClientTimeout(
        total=120
    )


    print(
        f"[VERCEL] Starting deployment: "
        f"{project_name}"
    )


    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                url,
                headers=headers,
                json=payload
            ) as response:

                response_text = (
                    await response.text()
                )


                try:

                    data = await response.json(
                        content_type=None
                    )

                except Exception:

                    data = {}


                if response.status in (
                    200,
                    201
                ):

                    deployment_url = (
                        data.get("url")
                    )


                    if deployment_url:

                        if not deployment_url.startswith(
                            "http"
                        ):

                            deployment_url = (
                                "https://"
                                + deployment_url
                            )


                        print(
                            f"[VERCEL] Deployment successful: "
                            f"{deployment_url}"
                        )


                        return deployment_url


                    print(
                        "[VERCEL] "
                        "Відповідь без URL:",
                        response_text
                    )

                    return None


                print(
                    f"[VERCEL ERROR] "
                    f"HTTP {response.status}: "
                    f"{response_text}"
                )

                return None


    except Exception as error:

        print("")
        print("========================================")
        print("🔥 VERCEL EXCEPTION")
        print("========================================")

        print(
            f"Type: {type(error).__name__}"
        )

        print(
            f"Message: {error!r}"
        )

        traceback.print_exc()

        print("========================================")

        return None


# ============================================================
# PROCESS ONE ORDER
# ============================================================

async def process_order(
    message: discord.Message
):

    if message.id in active_orders:

        return


    active_orders.add(
        message.id
    )


    try:

        budget = extract_budget(
            message.content
        )


        print(
            f"[NEW ORDER] "
            f"{message.author} | "
            f"Budget: ${budget}"
        )


        # ====================================================
        # CREATE THREAD
        # ====================================================

        try:

            thread = await message.create_thread(

                name=(
                    f"Grox Order ${budget} | "
                    f"{message.author.name}"
                ),

                auto_archive_duration=1440
            )


        except discord.Forbidden:

            await message.channel.send(

                f"{message.author.mention}, "
                f"я знайшов ваше замовлення, "
                f"але мені не вистачає прав "
                f"для створення thread."
            )


            print(
                "[DISCORD ERROR] "
                "Немає прав на створення thread."
            )

            return


        except Exception as error:

            print(
                "[THREAD ERROR]",
                repr(error)
            )

            traceback.print_exc()


            await message.channel.send(

                f"{message.author.mention}, "
                f"я знайшов ваше замовлення, "
                f"але виникла технічна помилка."
            )

            return


        # ====================================================
        # FIRST MESSAGE
        # ====================================================

        await thread.send(

            f"👋 Вітаю, {message.author.mention}!\n\n"

            f"🤖 **Grox прийняв ваше замовлення.**\n"

            f"💰 Орієнтовний бюджет: "
            f"**${budget}**\n\n"

            f"📋 Надішліть сюди детальне ТЗ:\n"

            f"• що саме потрібно створити;\n"
            f"• які функції потрібні;\n"
            f"• який дизайн ви хочете;\n"
            f"• інші важливі вимоги.\n\n"

            f"Після отримання ТЗ я запущу "
            f"автоматичну обробку."
        )


        # ====================================================
        # WAIT FOR CLIENT
        # ====================================================

        def check(
            msg: discord.Message
        ):

            return (

                msg.author.id
                == message.author.id

                and

                msg.channel.id
                == thread.id

                and

                not msg.author.bot
            )


        try:

            client_message = await bot.wait_for(

                "message",

                check=check,

                timeout=CLIENT_TIMEOUT
            )


        except asyncio.TimeoutError:

            await thread.send(

                "⏰ Час очікування ТЗ минув.\n"

                "Якщо ви все ще хочете "
                "продовжити замовлення — "
                "надішліть повідомлення "
                "в цьому thread."
            )


            print(
                f"[TIMEOUT] "
                f"{message.author}"
            )

            return


        # ====================================================
        # START GENERATION
        # ====================================================

        await thread.send(

            "📋 **ТЗ отримано!**\n\n"

            "🤖 Аналізую вимоги...\n"
            "💻 Генерую сайт...\n"
            "🚀 Готую деплой..."
        )


        # ====================================================
        # GENERATE HTML
        # ====================================================

        try:

            html_code = (
                await generate_site_code(
                    client_message.content
                )
            )


        except Exception as error:

            print("")
            print("========================================")
            print("🔥 ORDER → GEMINI ERROR")
            print("========================================")

            print(
                f"Type: {type(error).__name__}"
            )

            print(
                f"Message: {error!r}"
            )

            traceback.print_exc()

            print("========================================")


            await thread.send(

                "❌ **Не вдалося згенерувати сайт.**\n\n"

                "🔎 Я записав повну технічну "
                "помилку в Render Logs.\n\n"

                "🛠️ Потрібно перевірити помилку "
                "Gemini перед наступною спробою."
            )

            return


        # ====================================================
        # VERCEL PROJECT
        # ====================================================

        project_name = (

            f"grox-job-"

            f"{message.author.id}-"

            f"{uuid.uuid4().hex[:8]}"
        )


        await thread.send(

            "🚀 **Код готовий.**\n\n"

            "Виконую автоматичний деплой "
            "на Vercel..."
        )


        # ====================================================
        # DEPLOY
        # ====================================================

        live_url = (
            await deploy_to_vercel(
                project_name,
                html_code
            )
        )


        # ====================================================
        # RESULT
        # ====================================================

        if live_url:

            await thread.send(

                "🎉 **ЗАМОВЛЕННЯ ГОТОВЕ!**\n\n"

                "✅ Сайт успішно згенерований.\n"
                "✅ Код підготовлений.\n"
                "✅ Проєкт відправлений на Vercel.\n\n"

                f"🔗 **Ваш сайт:** "
                f"{live_url}\n\n"

                "Дякую за замовлення! 🤖"
            )


            print(

                f"[SUCCESS] "
                f"{message.author} -> "
                f"{live_url}"
            )


        else:

            await thread.send(

                "⚠️ Код сайту успішно "
                "згенерований, "

                "але Vercel не повернув "
                "адресу деплою.\n\n"

                "Адміністратор повинен "
                "перевірити Vercel."
            )


            print(
                "[DEPLOY FAILED]"
            )


    except Exception as error:

        print("")
        print("========================================")
        print("🔥 ORDER ERROR")
        print("========================================")

        print(
            f"Type: {type(error).__name__}"
        )

        print(
            f"Message: {error!r}"
        )

        traceback.print_exc()

        print("========================================")


        try:

            await message.channel.send(

                f"{message.author.mention}, "
                f"під час обробки замовлення "
                f"сталася технічна помилка."
            )


        except Exception:

            pass


    finally:

        active_orders.discard(
            message.id
        )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    print(
        "========================================"
    )

    print(
        f"🤖 Grox ONLINE: {bot.user}"
    )

    print(
        f"🆔 Bot ID: {bot.user.id}"
    )

    print(
        f"🧠 Gemini model: {GEMINI_MODEL}"
    )

    print(
        f"📡 Client channel: "
        f"{CLIENT_CHANNEL_ID}"
    )

    print(
        "🚀 Grox готовий приймати замовлення!"
    )

    print(
        "========================================"
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    # --------------------------------------------------------
    # IGNORE BOTS
    # --------------------------------------------------------

    if message.author.bot:

        return


    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    await bot.process_commands(
        message
    )


    # --------------------------------------------------------
    # ONLY CLIENT CHANNEL
    # --------------------------------------------------------

    if (
        message.channel.id
        != CLIENT_CHANNEL_ID
    ):

        return


    # --------------------------------------------------------
    # CHECK ORDER
    # --------------------------------------------------------

    if not is_order_message(
        message.content
    ):

        return


    # --------------------------------------------------------
    # PREVENT DUPLICATES
    # --------------------------------------------------------

    if message.id in active_orders:

        return


    # --------------------------------------------------------
    # START ORDER
    # --------------------------------------------------------

    print(
        f"[ORDER DETECTED] "
        f"{message.author}: "
        f"{message.content}"
    )


    asyncio.create_task(
        process_order(message)
    )


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

async def health_handler(
    request
):

    return web.Response(
        text="Grox is running! 🤖"
    )


async def start_health_server():

    app = web.Application()


    app.router.add_get(
        "/",
        health_handler
    )


    runner = web.AppRunner(
        app
    )


    await runner.setup()


    site = web.TCPSite(

        runner,

        "0.0.0.0",

        PORT
    )


    await site.start()


    print(
        f"[HEALTH] "
        f"Server listening on port {PORT}"
    )


    return runner


# ============================================================
# MAIN
# ============================================================

async def main():

    print(
        "🚀 Starting Grox..."
    )


    health_runner = (
        await start_health_server()
    )


    try:

        await bot.start(
            DISCORD_TOKEN
        )


    finally:

        await bot.close()

        await health_runner.cleanup()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )


    except KeyboardInterrupt:

        print(
            "🛑 Grox stopped."
        )
