import os
import asyncio
import threading
from flask import Flask
import discord
from discord.ext import commands
import requests

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Бот успішно запущений як {bot.user}')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.DMChannel) or bot.user in message.mentions:
        if not GEMINI_API_KEY:
            await message.channel.send("Помилка: GEMINI_API_KEY не налаштовано в Render.")
            return

        async with message.channel.typing():
            try:
                await asyncio.sleep(1)
                user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if not user_text:
                    user_text = "Привіт"

                # Оновлена модель Gemini
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY.strip()}"
                payload = {"contents": [{"parts": [{"text": user_text}]}]}
                headers = {'Content-Type': 'application/json'}
                
                response = requests.post(url, json=payload, headers=headers, timeout=15)
                result = response.json()

                if "candidates" in result and len(result["candidates"]) > 0:
                    reply_text = result["candidates"][0]["content"]["parts"][0]["text"]
                    await message.channel.send(reply_text)
                elif "error" in result:
                    err_msg = result["error"].get("message", "Невідома помилка API")
                    await message.channel.send(f"Помилка Gemini API: {err_msg}")
                else:
                    await message.channel.send("Отримано порожню відповідь від Gemini.")

            except Exception as e:
                await message.channel.send(f"Системна помилка: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
