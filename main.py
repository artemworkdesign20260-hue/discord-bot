import os
import asyncio
import discord
from discord.ext import commands
import requests

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
            await message.channel.send("Помилка: GEMINI_API_KEY не налаштовано.")
            return

        async with message.channel.typing():
            try:
                await asyncio.sleep(1)
                user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if not user_text:
                    user_text = "Привіт"

                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                payload = {"contents": [{"parts": [{"text": user_text}]}]}
                
                response = requests.post(url, json=payload, timeout=10)
                result = response.json()

                if "candidates" in result:
                    reply_text = result["candidates"][0]["content"]["parts"][0]["text"]
                    await message.channel.send(reply_text)
                else:
                    print(f"Помилка API: {result}")
                    await message.channel.send("Виникла помилка при зверненні до Gemini.")

            except Exception as e:
                print(f"Помилка: {e}")
                await message.channel.send(f"Виникла помилка: {e}")

    await bot.process_commands(message)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
