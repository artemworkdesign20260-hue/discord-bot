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

    app.run(
        host="0.0.0.0",
        port=port
    )


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
# GROX SETTINGS
# =========================================================

# Скільки повідомлень Grox пам'ятає в одному діалозі
MAX_HISTORY_MESSAGES = 20


# Пам'ять діалогів
# Ключ = ID каналу
conversation_history = {}


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

async def ask_gemini(
    user_text,
    history
):
    """
    Відправляє повідомлення Gemini
    разом із попереднім контекстом діалогу.
    """

    if not GEMINI_API_KEY:

        return (
            "Помилка: GEMINI_API_KEY не налаштований."
        )


    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )


    # -----------------------------------------------------
    # СИСТЕМНА ІНСТРУКЦІЯ GROX
    # -----------------------------------------------------

    system_instruction = """
Ти — Grox, Discord-бот для неофіційного фріланс-сервісу.

Твоя задача — допомагати користувачам вести та організовувати
фріланс-угоди між клієнтом і виконавцем.

Будь уважним до:
- умов угоди;
- ціни;
- опису роботи;
- строків;
- сторін угоди;
- підтверджень користувачів.

Не вигадуй факти, яких користувач не повідомляв.

Якщо інформації недостатньо — попроси користувача
уточнити необхідні дані.

Відповідай зрозуміло, коротко та по суті.

Поки спеціальна система угод ще не активована,
не стверджуй, що гроші були отримані, робота виконана
або угода завершена, якщо це фактично не підтверджено
системою.
"""


    # -----------------------------------------------------
    # ФОРМУЄМО CONTEXT
    # -----------------------------------------------------

    contents = []


    # Системна інструкція
    contents.append(
        {
            "role": "user",
            "parts": [
                {
                    "text": system_instruction
                }
            ]
        }
    )


    # Попередня історія
    for item in history:

        contents.append(
            {
                "role": item["role"],
                "parts": [
                    {
                        "text": item["text"]
                    }
                ]
            }
        )


    # Нове повідомлення
    contents.append(
        {
            "role": "user",
            "parts": [
                {
                    "text": user_text
                }
            ]
        }
    )


    payload = {
        "contents": contents
    }


    headers = {
        "Content-Type": "application/json"
    }


    last_error = None


    # -----------------------------------------------------
    # ДО 3 СПРОБ
    # -----------------------------------------------------

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
            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                last_error = (
                    f"Gemini HTTP {response.status_code}"
                )


                if attempt < 2:

                    await asyncio.sleep(
                        2 ** attempt
                    )

                    continue


                return (
                    "Gemini зараз тимчасово "
                    "перевантажений. "
                    "Спробуй ще раз через кілька секунд."
                )


            # -------------------------------------------------
            # JSON
            # -------------------------------------------------

            result = response.json()


            # -------------------------------------------------
            # API ERROR
            # -------------------------------------------------

            if "error" in result:

                error_message = result["error"].get(
                    "message",
                    "Невідома помилка Gemini API"
                )


                last_error = error_message


                if attempt < 2:

                    await asyncio.sleep(
                        2 ** attempt
                    )

                    continue


                return (
                    f"Помилка Gemini API: "
                    f"{error_message}"
                )


            # -------------------------------------------------
            # NORMAL RESPONSE
            # -------------------------------------------------

            candidates = result.get(
                "candidates",
                []
            )


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


            last_error = (
                "Gemini повернув порожню відповідь."
            )


            if attempt < 2:

                await asyncio.sleep(
                    2 ** attempt
                )

                continue


        except requests.exceptions.Timeout:

            last_error = "Gemini timeout"


            if attempt < 2:

                await asyncio.sleep(
                    2 ** attempt
                )

                continue


        except requests.exceptions.RequestException as e:

            last_error = str(e)


            if attempt < 2:

                await asyncio.sleep(
                    2 ** attempt
                )

                continue


        except Exception as e:

            last_error = str(e)

            break


    print(
        f"Gemini error: {last_error}"
    )


    return (
        "Не вдалося отримати відповідь "
        "від Gemini. Спробуй ще раз."
    )


# =========================================================
# DISCORD MESSAGE SPLITTER
# =========================================================

async def send_long_message(
    channel,
    text
):
    """
    Discord має обмеження приблизно
    2000 символів на одне повідомлення.
    """

    max_length = 1900


    while len(text) > max_length:

        # Спочатку шукаємо перенос рядка
        split_at = text.rfind(
            "\n",
            0,
            max_length
        )


        # Якщо немає — шукаємо пробіл
        if split_at <= 0:

            split_at = text.rfind(
                " ",
                0,
                max_length
            )


        # Якщо зовсім нічого немає
        if split_at <= 0:

            split_at = max_length


        part = text[:split_at].strip()


        await channel.send(
            part
        )


        text = text[
            split_at:
        ].strip()


    if text:

        await channel.send(
            text
        )


# =========================================================
# GET CHANNEL HISTORY
# =========================================================

def get_history(channel_id):

    if channel_id not in conversation_history:

        conversation_history[
            channel_id
        ] = []


    return conversation_history[
        channel_id
    ]


# =========================================================
# SAVE MESSAGE TO HISTORY
# =========================================================

def add_to_history(
    channel_id,
    role,
    text
):

    history = get_history(
        channel_id
    )


    history.append(
        {
            "role": role,
            "text": text
        }
    )


    # Обмежуємо пам'ять
    if len(history) > MAX_HISTORY_MESSAGES:

        conversation_history[
            channel_id
        ] = history[
            -MAX_HISTORY_MESSAGES:
        ]


# =========================================================
# CLEAR HISTORY
# =========================================================

def clear_history(channel_id):

    conversation_history.pop(
        channel_id,
        None
    )


# =========================================================
# MESSAGES
# =========================================================

@bot.event
async def on_message(message):

    # -----------------------------------------------------
    # НЕ ВІДПОВІДАЄМО САМОМУ СОБІ
    # -----------------------------------------------------

    if message.author == bot.user:

        return


    # -----------------------------------------------------
    # ВИЗНАЧАЄМО ПРИВАТНИЙ КАНАЛ
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


    # -----------------------------------------------------
    # DM
    # -----------------------------------------------------

    is_dm = isinstance(
        message.channel,
        discord.DMChannel
    )


    # -----------------------------------------------------
    # ДОЗВОЛЕНІ МІСЦЯ
    # -----------------------------------------------------

    # Якщо це не DM і не приватний канал —
    # Grox мовчить.

    if (
        not is_dm
        and not is_private_client_channel
    ):

        await bot.process_commands(
            message
        )

        return


    # -----------------------------------------------------
    # GEMINI KEY
    # -----------------------------------------------------

    if not GEMINI_API_KEY:

        await message.channel.send(
            "Помилка: GEMINI_API_KEY "
            "не налаштований у Render Environment."
        )

        return


    # -----------------------------------------------------
    # ОЧИЩАЄМО @GROX
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
    # CHANNEL MEMORY
    # -----------------------------------------------------

    channel_id = message.channel.id


    history = get_history(
        channel_id
    )


    # -----------------------------------------------------
    # ДОДАЄМО ПОВІДОМЛЕННЯ КОРИСТУВАЧА
    # -----------------------------------------------------

    add_to_history(
        channel_id,
        "user",
        user_text
    )


    # -----------------------------------------------------
    # GEMINI
    # -----------------------------------------------------

    async with message.channel.typing():

        try:

            reply_text = await ask_gemini(
                user_text,
                history[:-1]
            )


            # -------------------------------------------------
            # ЗБЕРІГАЄМО ВІДПОВІДЬ GROX
            # -------------------------------------------------

            add_to_history(
                channel_id,
                "model",
                reply_text
            )


            # -------------------------------------------------
            # ВІДПРАВЛЯЄМО В DISCORD
            # -------------------------------------------------

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


    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

    await bot.process_commands(
        message
    )


# =========================================================
# COMMAND: CLEAR MEMORY
# =========================================================

@bot.command(
    name="clear"
)
async def clear_memory(ctx):

    # Команда доступна тільки там,
    # де Grox і так має право працювати.

    is_private = False


    if CLIENT_CHANNEL_ID:

        try:

            is_private = (
                ctx.channel.id
                == int(CLIENT_CHANNEL_ID)
            )

        except ValueError:

            pass


    is_dm = isinstance(
        ctx.channel,
        discord.DMChannel
    )


    if not is_dm and not is_private:

        return


    clear_history(
        ctx.channel.id
    )


    await ctx.send(
        "Пам'ять цієї розмови очищена."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    # -----------------------------------------------------
    # FLASK FOR RENDER
    # -----------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )


    flask_thread.start()


    # -----------------------------------------------------
    # DISCORD TOKEN CHECK
    # -----------------------------------------------------

    if not DISCORD_TOKEN:

        print(
            "ПОМИЛКА: DISCORD_TOKEN "
            "не знайдений у Render Environment."
        )


    else:

        bot.run(
            DISCORD_TOKEN
    )
