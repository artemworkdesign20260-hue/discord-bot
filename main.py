import os
import asyncio
import discord
from discord.ext import commands
from google import genai

# Налаштування Gemini API (новий SDK)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    client = None

# Налаштування Discord бота
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

    # Відповідь у приватні повідомлення або згадування бота
    if isinstance(message.channel, discord.DMChannel) or bot.user in message.mentions:
        if not client:
            await message.channel.send("Помилка: GEMINI_API_KEY не налаштовано в Environment Variables.")
            return

        async with message.channel.typing():
            try:
                # Затримка перед відповіддю для імітації людини
                await asyncio.sleep(2)

                prompt = (
                    "Ти менеджер з продажів послуг розробки. Твоя мета: "
                    "ввічливо спілкуватися, дізнаватися потреби клієнта, "
                    "пропонувати відповідні рішення та домовлятися про ціну. "
                    f"Повідомлення клієнта: {message.content}"
                )

                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                )
                await message.channel.send(response.text)
            except Exception as e:
                await message.channel.send("Вибачте, виникла помилка при обробці запиту.")
                print(f"Помилка Gemini: {e}")

    await bot.process_commands(message)

# Запуск бота
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("Помилка: DISCORD_TOKEN не знайдено в Environment Variables!")

