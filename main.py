import os
import asyncio
import threading
import requests

from flask import Flask
import discord
from discord.ext import commands


# =========================================================
# RENDER / FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Grox is running!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ID приватного каналу, де Grox працює без @Grox
CLIENT_CHANNEL_ID = os.environ.get("CLIENT_CHANNEL_ID")

# Модель Gemini
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# START
# =========================================================

@bot.event
async def on_ready():
    print("--------------------------------")
    print(f"Grox запущений як: {bot.user}")
    print(f"Gemini model: {GEMINI_MODEL}")
    print(f"Client channel ID: {CLIENT_CHANNEL_ID}")
    print("--------------------------------")


# =========================================================
# GEMINI
# =========================================================

async def ask_gemini(user_text):
    """
    Відправляє повідомлення Gemini.
    Запит запускається через asyncio.to_thread(),
    тому Discord event loop не блокується.
    """

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": user_text
                    }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json"
    }

    last_error = None

    # До 3 спроб
    for attempt in range(3):

        try:

            response = await asyncio.to_thread(
                requests.post,
                url,
                json=payload,
                headers=headers,
                timeout=30
            )

            # Тимчасові серверні помилки
            if response.status_code in (429, 500, 502, 503, 504):

                last_error = (
                    f"Gemini HTTP {response.status_code}"
                )

                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue

                return (
                    "Gemini зараз тимчасово перевантажений. "
                    "Спробуй ще раз через кілька секунд."
                )

            result = response.json()

            # API повернув помилку
            if "error" in result:

                error_message = result["error"].get(
                    "message",
                    "Невідома помилка Gemini API"
                )

                last_error = error_message

                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue

                return (
                    f"Помилка Gemini API: {error_message}"
                )

            # Нормальна відповідь
            candidates = result.get("candidates", [])

            if candidates:

                content = candidates[0].get(
                    "content",
                    {}
                )

                parts = content.get(
                    "parts",
                    []
                )

                if parts:

                    text = parts[0].get(
                        "text",
                        ""
                    )

                    if text.strip():
                        return text.strip()

            last_error = "Gemini повернув порожню відповідь."

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue

        except requests.exceptions.Timeout:

            last_error = "Gemini timeout"

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue

        except requests.exceptions.RequestException as e:

            last_error = str(e)

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue

        except Exception as e:

            last_error = str(e)
            break

    print(f"Gemini error: {last_error}")

    return (
        "Не вдалося отримати відповідь від Gemini. "
        "Спробуй ще раз."
    )


# =========================================================
# DISCORD MESSAGE SPLITTER
# =========================================================

async def send_long_message(channel, text):
    """
    Discord має обмеження приблизно 2000 символів
    на одне повідомлення.
    """

    max_length = 1900

    while len(text) > max_length:

        # Намагаємося розділити по переносу рядка
        split_at = text.rfind(
            "\n",
            0,
            max_length
        )

        # Якщо переносу немає — по пробілу
        if split_at <= 0:
            split_at = text.rfind(
                " ",
                0,
                max_length
            )

        # Якщо нічого не знайшли
        if split_at <= 0:
            split_at = max_length

        part = text[:split_at].strip()

        await channel.send(part)

        text = text[split_at:].strip()

    if text:
        await channel.send(text)


# =========================================================
# MESSAGES
# =========================================================

@bot.event
async def on_message(message):

    # Не відповідаємо самому собі
    if message.author == bot.user:
        return

    # -----------------------------------------------------
    # ВИЗНАЧАЄМО, ЧИ МОЖЕ GROX ВІДПОВІДАТИ
    # -----------------------------------------------------

    is_private_client_channel = False

    if CLIENT_CHANNEL_ID:

        try:
            configured_channel_id = int(
                CLIENT_CHANNEL_ID
            )

            is_private_client_channel = (
                message.channel.id
                == configured_channel_id
            )

        except ValueError:
            print(
                "Помилка: CLIENT_CHANNEL_ID "
                "має бути числом."
            )

    # Особисті повідомлення також дозволені
    is_dm = isinstance(
        message.channel,
        discord.DMChannel
    )

    # Якщо це не DM і не наш приватний канал —
    # Grox мовчить
    if not is_dm and not is_private_client_channel:
        await bot.process_commands(message)
        return

    # -----------------------------------------------------
    # ПЕРЕВІРКА GEMINI KEY
    # -----------------------------------------------------

    if not GEMINI_API_KEY:

        await message.channel.send(
            "Помилка: GEMINI_API_KEY не налаштований "
            "у Render Environment."
        )

        return

    # -----------------------------------------------------
    # ОЧИЩАЄМО @GROX, ЯКЩО ВІН РАПТОМ Є
    # -----------------------------------------------------

    user_text = message.content

    if bot.user:

        user_text = user_text.replace(
            f"<@{bot.user.id}>",
            ""
        )

        user_text = user_text.replace(
            f"<@!{bot.user.id}>",
            ""
        )

    user_text = user_text.strip()

    if not user_text:
        user_text = "Привіт"

    # -----------------------------------------------------
    # GEMINI
    # -----------------------------------------------------

    async with message.channel.typing():

        try:

            reply_text = await ask_gemini(
                user_text
            )

            await send_long_message(
                message.channel,
                reply_text
            )

        except Exception as e:

            print(
                f"Message error: {e}"
            )

            await message.channel.send(
                "Сталася тимчасова помилка. "
                "Спробуй ще раз."
            )

    await bot.process_commands(message)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    # Flask для Render
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Перевірка Discord token
    if not DISCORD_TOKEN:

        print(
            "ПОМИЛКА: DISCORD_TOKEN "
            "не знайдений у Render Environment."
        )

    else:

        bot.run(DISCORD_TOKEN)
