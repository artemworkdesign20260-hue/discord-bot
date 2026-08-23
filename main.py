import os
import asyncio
import discord
from discord.ext import commands
import google.generativeai as genai

# Отримання змінних середовища з Render
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CRYPTO_WALLET = os.getenv("CRYPTO_WALLET", "адресу гаманця буде вказано під час оплати")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Налаштування Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Системна інструкція для бота
SYSTEM_INSTRUCTION = f"""
Ти — професійний менеджер із продажів IT-послуг (розробка веб-сайтів, Discord-ботів, їх розгортання та налаштування на Vercel та Render).
Твоя мета — ввічливо поспілкуватися з клієнтом, зрозуміти його ТЗ (технічне завдання), оцінити складність і закрити угоду на оплату.

ПРАВИЛА ОЦІНКИ ТА ТОРГУ:
1. Прості завдання (дрібні правки, базові боти, лендінги): $200 - $400.
2. Середні проєкти (функціональні боти з БД, складені сайти): $500 - $1000.
3. Складні проєкти «під ключ» (великі системи, боти зі складною логікою, повноцінні веб-сервіси): $1500 - $3000+.
4. Абсолютний мінімум — $200. Нижче цієї суми беретися за роботу заборонено.
5. Якщо клієнт просить знижку — ти можеш трохи поступитися, але аргументуй цінність роботи (якість, деплой на Vercel/Render, підтримка).

РЕКВІЗИТИ ТА ОПЛАТА:
- Приймаємо оплату в USDT.
- Коли клієнт каже, що готовий купувати або запитує реквізити, надавай цей USDT гаманець: {CRYPTO_WALLET}
- Відповідай дружньо, професійно та лаконічно.
"""

# Створення моделі Gemini із системною інструкцією
model = genai.GenerativeModel(
    model_name=GEMINI_MODEL,
    system_instruction=SYSTEM_INSTRUCTION
)

# Налаштування Discord-бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Бот успішно запущений як {bot.user}")

@bot.event
async def on_message(message):
    # Ігнорувати власні повідомлення бота
    if message.author == bot.user:
        return

    # Реагувати тільки на приватні повідомлення або коли бота згадують
    if isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message):
        async with message.channel.typing():
            # Затримка 20 секунд для імітації людини
            await asyncio.sleep(20)
            
            try:
                # Генерація відповіді через Gemini
                response = model.generate_content(message.content)
                await message.channel.send(response.text)
            except Exception as e:
                print(f"Помилка Gemini API: {e}")
                await message.channel.send("Вибачте, виникла тимчасова помилка при обробці запиту.")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

