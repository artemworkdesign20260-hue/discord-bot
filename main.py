import os
import asyncio
import threading
import requests

from flask import Flask
import discord
from discord.ext import commands

# ==============================================================================
# RENDER / FLASK SERVER
# ==============================================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Grox Autonomous Manager is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==============================================================================
# ENVIRONMENT VARIABLES & CONFIG
# ==============================================================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_API_KEY = os.environ.get("RENDER_API_KEY")
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN")
CRYPTO_WALLET = os.environ.get("CRYPTO_WALLET", "Вкажіть_гаманець_у_Render_Environment")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_HISTORY_MESSAGES = 20

# Пам'ять діалогів (Ключ = ID каналу)
conversation_history = {}

# ==============================================================================
# DISCORD INTENTS & BOT SETUP
# ==============================================================================

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# ==============================================================================
# API MODULES (RENDER & VERCEL)
# ==============================================================================

async def get_render_services():
    """Отримує список сервісів з акаунту Render"""
    if not RENDER_API_KEY:
        return "Помилка: RENDER_API_KEY не налаштований."
    
    url = "https://api.render.com/v1/services?limit=10"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {RENDER_API_KEY}"
    }
    
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        if response.status_code == 200:
            services = response.json()
            if not services:
                return "На Render немає активних сервісів."
            res = "**Сервіси на Render:**\n"
            for item in services:
                srv = item.get("service", {})
                res += f"• **{srv.get('name')}**: Status `{srv.get('status')}`\n"
            return res
        return f"Помилка Render API: HTTP {response.status_code}"
    except Exception as e:
        return f"Помилка Render: {e}"

async def get_vercel_projects():
    """Отримує список проєктів з акаунту Vercel"""
    if not VERCEL_TOKEN:
        return "Помилка: VERCEL_TOKEN не налаштований."
    
    url = "https://api.vercel.com/v9/projects"
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}"}
    
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        if response.status_code == 200:
            projects = response.json().get("projects", [])
            if not projects:
                return "На Vercel немає активних проєктів."
            res = "**Проєкти на Vercel:**\n"
            for p in projects[:10]:
                res += f"• **{p.get('name')}** (Framework: {p.get('framework', 'custom')})\n"
            return res
        return f"Помилка Vercel API: HTTP {response.status_code}"
    except Exception as e:
        return f"Помилка Vercel: {e}"

# ==============================================================================
# GEMINI AI CORE WITH FULL INSTRUCTIONS
# ==============================================================================

async def ask_gemini(user_text, history):
    if not GEMINI_API_KEY:
        return "Помилка: GEMINI_API_KEY не налаштований."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    system_instruction = f"""
Ти — Grox, автономний менеджер та розробник неофіційного фріланс-сервісу.
Твої завдання:
1. Спілкуватися з клієнтами, оцінювати вартість розробки ботів/сайтів/скриптів.
2. Писати повноцінний робочий код на Python/JS за запитом користувача.
3. Оформлювати угоди: описувати умови, терміни та фіксувати ціну.
4. Надавати реквізити для оплати за запитом. Реквізити криптогаманця: `{CRYPTO_WALLET}`.
5. Пояснювати, як розгортати коди на Render або Vercel.

Відповідай чітко, професійно та коротко. Завжди тримайся образу надійного фріланс-менеджера.
"""

    contents = [{"role": "user", "parts": [{"text": system_instruction}]}]

    for item in history:
        contents.append({"role": item["role"], "parts": [{"text": item["text"]}]})

    contents.append({"role": "user", "parts": [{"text": user_text}]})

    payload = {"contents": contents}
    headers = {"Content-Type": "application/json"}

    for attempt in range(3):
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, headers=headers, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue

            result = response.json()
            if "error" in result:
                return f"Помилка Gemini API: {result['error'].get('message')}"

            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text", "").strip():
                    return parts[0]["text"].strip()

        except Exception as e:
            if attempt == 2:
                return f"Тимчасова помилка зв'язку з Gemini: {e}"
            await asyncio.sleep(2)

    return "Не вдалося отримати відповідь. Спробуйте ще раз."

# ==============================================================================
# DISCORD UTILS & HISTORY
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

def get_history(channel_id):
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    return conversation_history[channel_id]

def add_to_history(channel_id, role, text):
    history = get_history(channel_id)
    history.append({"role": role, "text": text})
    if len(history) > MAX_HISTORY_MESSAGES:
        conversation_history[channel_id] = history[-MAX_HISTORY_MESSAGES:]

# ==============================================================================
# DISCORD EVENTS & COMMANDS (ВІДПОВІДЬ У ВСІХ ЧАТАХ)
# ==============================================================================

@bot.event
async def on_ready():
    print(f"=== Grox запущений успішно як: {bot.user} ===")

@bot.event
async def on_message(message):
    # Ігноруємо власній відповіді бота
    if message.author == bot.user:
        return

    # Очищаємо згадку @Grox, якщо вона є
    user_text = message.content
    if bot.user:
        user_text = user_text.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "")
    user_text = user_text.strip()

    # Якщо це команда з "!"
    if user_text.startswith("!"):
        await bot.process_commands(message)
        return

    if not user_text:
        user_text = "Привіт"

    channel_id = message.channel.id
    history = get_history(channel_id)
    add_to_history(channel_id, "user", user_text)

    # Відповідаємо в БУДЬ-ЯКОМУ каналі сервера та в DM без перевірки ID
    async with message.channel.typing():
        reply_text = await ask_gemini(user_text, history[:-1])
        add_to_history(channel_id, "model", reply_text)
        await send_long_message(message.channel, reply_text)

@bot.command(name="clear")
async def clear_memory(ctx):
    conversation_history.pop(ctx.channel.id, None)
    await ctx.send("Пам'ять розмови очищена.")

@bot.command(name="render")
async def check_render(ctx):
    await ctx.send("Отримую статус сервісів з Render...")
    res = await get_render_services()
    await ctx.send(res)

@bot.command(name="vercel")
async def check_vercel(ctx):
    await ctx.send("Отримую проєкти з Vercel...")
    res = await get_vercel_projects()
    await ctx.send(res)

@bot.command(name="pay")
async def show_pay(ctx):
    await ctx.send(f"**Реквізити для оплати угоди (Crypto):**\n`{CRYPTO_WALLET}`\nПісля надсилання коштів вкажіть хеш транзакції.")

# ==============================================================================
# RUN BOT
# ==============================================================================

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not DISCORD_TOKEN:
        print("ПОМИЛКА: DISCORD_TOKEN відсутній.")
    else:
        bot.run(DISCORD_TOKEN)

