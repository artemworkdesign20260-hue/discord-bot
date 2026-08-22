import os
import asyncio
import discord
from discord.ext import commands
from google import genai

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
            await message.channel.send("Помилка: GEMINI_API_KEY не налаштовано.")
            return

        async with message.channel.typing():
            try:
                await asyncio.sleep(1)
                
                # Очищаємо текст від згадки бота
                user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
                if not user_text:
                    user_text = "Привіт"

                # Запит до Gemini
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=user_text,
                )

                await message.channel.send(response.text)
            except Exception as e:
                print(f"Помилка Gemini: {e}")
                await message.channel.send(f"Виникла помилка: {e}")

    await bot.process_commands(message)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
