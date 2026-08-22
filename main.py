import os
import asyncio
import threading
from flask import Flask
import discord
from discord.ext import commands
from google import genai

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
        if not client:
            await message.channel.send("Помилка: GEMINI_API_KEY не налаштовано в Render.")
            return

        async with message.channel.typing():
            try:
                await asyncio.sleep(1)
                user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if not user_text:
                    user_text = "Привіт"

                # Використовуємо актуальну модель через офіційну бібліотеку Google
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=user_text,
                )

                if response.text:
                    await message.channel.send(response.text)
                else:
                    await message.channel.send("Отримано порожню відповідь від Gemini.")

            except Exception as e:
                await message.channel.send(f"Помилка Gemini API: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
