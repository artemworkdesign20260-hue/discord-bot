import os
import asyncio
import threading

from flask import Flask
import discord
from discord.ext import commands
import requests


# =========================
# Flask для Render
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# =========================
# Налаштування
# =========================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")


# =========================
# Discord
# =========================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================
# Коли бот запустився
# =========================

@bot.event
async def on_ready():
    print(f"Бот успішно запущений як {bot.user}")


# =========================
# Повідомлення
# =========================

@bot.event
async def on_message(message):

    # Не відповідаємо самому собі
    if message.author == bot.user:
        return

    # Працюємо тільки в ЛС або коли бота згадали
    if (
        isinstance(message.channel, discord.DMChannel)
        or bot.user in message.mentions
    ):

        # Перевіряємо API ключ Gemini
        if not GEMINI_API_KEY:
            await message.channel.send(
                "Помилка: API ключ Gemini не налаштовано в Render."
            )
            return

        async with message.channel.typing():

            try:

                # Прибираємо згадку бота з повідомлення
                user_text = message.content.replace(
                    f"<@{bot.user.id}>",
                    ""
                ).strip()

                if not user_text:
                    user_text = "Привіт"

                # Gemini API
                url = (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/models/gemini-3.6-flash:"
                    f"generateContent?key={GEMINI_API_KEY}"
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

                result = None

                # До 3 спроб
                for attempt in range(3):

                    try:

                        # ВАЖЛИВО:
                        # requests запускаємо окремо,
                        # щоб він не блокував Discord
                        response = await asyncio.to_thread(
                            requests.post,
                            url,
                            json=payload,
                            headers=headers,
                            timeout=30
                        )

                        result = response.json()

                        # Якщо отримали нормальну відповідь
                        if (
                            "candidates" in result
                            and len(result["candidates"]) > 0
                        ):
                            break

                        # Якщо Gemini тимчасово перевантажений
                        if "error" in result:

                            err_msg = result["error"].get(
                                "message",
                                ""
                            )

                            if (
                                "429" in str(err_msg)
                                or "high demand" in str(err_msg).lower()
                                or "503" in str(err_msg)
                            ):

                                if attempt < 2:
                                    await asyncio.sleep(
                                        2 * (attempt + 1)
                                    )
                                    continue

                    except requests.exceptions.Timeout:

                        if attempt < 2:
                            await asyncio.sleep(
                                2 * (attempt + 1)
                            )
                            continue

                        await message.channel.send(
                            "Gemini занадто довго не відповідає. "
                            "Спробуй ще раз."
                        )
                        return

                    except requests.exceptions.RequestException:

                        if attempt < 2:
                            await asyncio.sleep(
                                2 * (attempt + 1)
                            )
                            continue

                        await message.channel.send(
                            "Не вдалося підключитися до Gemini. "
                            "Спробуй ще раз."
                        )
                        return

                # =========================
                # Обробка відповіді Gemini
                # =========================

                if (
                    result
                    and "candidates" in result
                    and len(result["candidates"]) > 0
                ):

                    reply_text = (
                        result["candidates"][0]
                        ["content"]
                        ["parts"][0]
                        ["text"]
                    )

                    await message.channel.send(
                        reply_text
                    )

                elif result and "error" in result:

                    err_msg = result["error"].get(
                        "message",
                        "Невідома помилка Gemini API"
                    )

                    await message.channel.send(
                        f"Помилка Gemini API: {err_msg}"
                    )

                else:

                    await message.channel.send(
                        "Gemini не повернув відповідь. "
                        "Спробуй ще раз."
                    )

            except Exception as e:

                print(f"Помилка: {e}")

                await message.channel.send(
                    "На жаль, сталася тимчасова помилка. "
                    "Спробуй ще раз."
                )

    # Обробка Discord-команд
    await bot.process_commands(message)


# =========================
# Запуск
# =========================

if __name__ == "__main__":

    # Запускаємо Flask окремо
    t = threading.Thread(
        target=run_flask
    )

    t.daemon = True
    t.start()

    # Запускаємо Discord
    if not DISCORD_TOKEN:
        print("ПОМИЛКА: DISCORD_TOKEN не налаштовано в Render.")
    else:
        bot.run(DISCORD_TOKEN)
