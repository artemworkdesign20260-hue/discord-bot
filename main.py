import os
import asyncio
import threading
import requests

from flask import Flask
import discord
from discord.ext import commands

# ==============================================================================
# RENDER / FLASK
# ==============================================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Grox is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==============================================================================
# ENVIRONMENT VARIABLES
# ==============================================================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ID приватного каналу, де Grox працює без @Grox
CLIENT_CHANNEL_ID = os.environ.get("CLIENT_CHANNEL_ID")

# Модель Gemini
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# ==============================================================================
# GROX SETTINGS
# ==============================================================================

# Скільки повідомлень Grox пам'ятає в одному діалозі
MAX_HISTORY_MESSAGES = 20

# Пам'ять діалогів (Ключ = ID каналу)
conversation_history = {}

# ==============================================================================
# DISCORD INTENTS
# ==============================================================================

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# ==============================================================================
# START
# ==============================================================================

@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Grox запущений як: {bot.user}")
    print(f"Gemini model: {GEMINI_MODEL}")
    print(f"Client channel ID: {CLIENT_CHANNEL_ID}")
    print("----------------------------------------")

# ==============================================================================
# GEMINI API
# ==============================================================================

async def ask_gemini(user_text, history):
    if not GEMINI_API_KEY:
        return "Помилка: GEMINI_API_KEY не налаштований."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    system_instruction = """
Ти — Grox, Discord-бот для неофіційного фріланс-сервісу.
Твоя задача — допомагати користувачам вести та організовувати фріланс-угоди між клієнтом і виконавцем.

Будь уважним до:
- умов угоди;
- ціни;
- опису роботи;
- строків;
- сторін угоди;
- підтверджень користувачів.

Не вигадуй факти, яких користувач не повідомляв.
Якщо інформації недостатньо — попроси користувача уточнити необхідні дані.
Відповідай зрозуміло, коротко та по суті.
"""

    contents = []
    contents.append({
        "role": "user",
        "parts": [{"text": system_instruction}]
    })

    for item in history:
        contents.append({
            "role": item["role"],
            "parts": [{"text": item["text"]}]
        })

    contents.append({
        "role": "user",
        "parts": [{"text": user_text}]
    })

    payload = {"contents": contents}
    headers = {"Content-Type": "application/json"}

    last_error = None

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                requests.post,
                url,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"Gemini HTTP {response.status_code}"
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue

            result = response.json()

            if "error" in result:
                error_message = result["error"].get("message", "Невідома помилка Gemini API")
                last_error = error_message
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return f"Помилка Gemini API: {error_message}"

            candidates = result.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    if text.strip():
                        return text.strip()

            last_error = "Gemini повернув порожню відповідь."

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
    return "Не вдалося отримати відповідь від Gemini. Спробуй ще раз."

# ==============================================================================
# DISCORD MESSAGE SPLITTER
# ==============================================================================

async def send_long_message(channel, text):
    max_length = 1900
    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, max_length)
        if split_at <= 0:
            split_at = max_length

        part = text[:split_at].strip()
        if part:
            await channel.send(part)
        text = text[split_at:].strip()

    if text:
        await channel.send(text)

# ==============================================================================
# HISTORY MANAGEMENT
# ==============================================================================

def get_history(channel_id):
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    return conversation_history[channel_id]

def add_to_history(channel_id, role, text):
    history = get_history(channel_id)
    history.append({"role": role, "text": text})
    if len(history) > MAX_HISTORY_MESSAGES:
        conversation_history[channel_id] = history[-MAX_HISTORY_MESSAGES:]

def clear_history(channel_id):
    conversation_history.pop(channel_id, None)

# ==============================================================================
# MESSAGES HANDLER
# ==============================================================================

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    is_private_client_channel = False
    if CLIENT_CHANNEL_ID:
        try:
            configured_channel_id = int(CLIENT_CHANNEL_ID)
            is_private_client_channel = (message.channel.id == configured_channel_id)
        except ValueError:
            pass

    is_dm = isinstance(message.channel, discord.DMChannel)

    # Якщо це не DM і не вказаний приватний канал — ігноруємо
    if not is_dm and not is_private_client_channel:
        await bot.process_commands(message)
        return

    # Очищаємо згадку @Grox, якщо вона є
    user_text = message.content
    if bot.user:
        user_text = user_text.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "")
    user_text = user_text.strip()

    if not user_text:
        user_text = "Привіт"

    channel_id = message.channel.id
    history = get_history(channel_id)
    add_to_history(channel_id, "user", user_text)

    async with message.channel.typing():
        try:
            reply_text = await ask_gemini(user_text, history[:-1])
            add_to_history(channel_id, "model", reply_text)
            await send_long_message(message.channel, reply_text)
        except Exception as e:
            print(f"Message error: {e}")
            await message.channel.send("Сталася тимчасова помилка. Спробуй ще раз.")

    await bot.process_commands(message)

# ==============================================================================
# COMMAND: CLEAR MEMORY
# ==============================================================================

@bot.command(name="clear")
async def clear_memory(ctx):
    is_private = False
    if CLIENT_CHANNEL_ID:
        try:
            is_private = (ctx.channel.id == int(CLIENT_CHANNEL_ID))
        except ValueError:
            pass

    is_dm = isinstance(ctx.channel, discord.DMChannel)

    if not is_dm and not is_private:
        return

    clear_history(ctx.channel.id)
    await ctx.send("Пам'ять цієї розмови очищена.")

# ==============================================================================
# RUN
# ==============================================================================

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not DISCORD_TOKEN:
        print("ПОМИЛКА: DISCORD_TOKEN не знайдений у Render Environment.")
    else:
        bot.run(DISCORD_TOKEN)

