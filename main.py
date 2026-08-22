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
            await message.channel.send("Помилка: API ключ не налаштовано.")
            return

        async with message.channel.typing():
            try:
                await asyncio.sleep(1)
                user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if not user_text:
                    user_text = "Привіт"

                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY.strip()}"
                payload = {"contents": [{"parts": [{"text": user_text}]}]}
                headers = {'Content-Type': 'application/json'}
                
                # Робимо до 2 спроб запиту, якщо сервери Google зайняті
                result = None
                for _ in range(2):
                    response = requests.post(url, json=payload, headers=headers, timeout=60)
                    result = response.json()
                    if "candidates" in result:
                        break
                    await asyncio.sleep(2)

                if "candidates" in result and len(result["candidates"]) > 0:
                    reply_text = result["candidates"][0]["content"]["parts"][0]["text"]
                    await message.channel.send(reply_text)
                elif "error" in result:
                    err_msg = result["error"].get("message", "")
                    if "high demand" in err_msg or "429" in str(result):
                        await message.channel.send("Вибач, сервери Google зараз трохи перевантажені. Спробуй написати мені ще раз через кілька секунд!")
                    else:
                        await message.channel.send("Ой, виникла тимчасова проблема з відповіддю. Спробуй ще раз!")
                else:
                    await message.channel.send("Отримано порожню відповідь від сервера.")

            except Exception as e:
                await message.channel.send("На жаль, стався збій підключення. Напиши мені ще раз!")

    await bot.process_commands(message)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
