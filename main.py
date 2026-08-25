import os
import asyncio
import logging
import threading
from flask import Flask
import discord
from discord.ext import commands
from google import genai
from google.genai import types

# ==============================================================================
# 1. ВЕБ-СЕРВЕР ДЛЯ RENDER (ЩОБ НЕ БУЛО ПОМИЛКИ PORT BINDING)
# ==============================================================================

app = Flask('')

@app.route('/')
def home():
    return "Grox Discord Bot is online!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ==============================================================================
# 2. НАЛАШТУВАННЯ ТА ЗМІННІ СЕРЕДОВИЩА
# ==============================================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CRYPTO_WALLET = os.getenv(
    "CRYPTO_WALLET",
    "адресу гаманця буде вказано під час оплати"
)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ==============================================================================
# 3. НАЛАШТУВАННЯ ЛОГУВАННЯ
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("GroxBot")

# ==============================================================================
# 4. ПЕРЕВІРКА НАЯВНОСТІ КЛЮЧІВ
# ==============================================================================

if not DISCORD_TOKEN:
    logger.error("❌ DISCORD_TOKEN не знайдено в змінних оточення!")
    raise RuntimeError("Не знайдено DISCORD_TOKEN у Render Environment.")

if not GEMINI_API_KEY:
    logger.error("❌ GEMINI_API_KEY не знайдено в змінних оточення!")
    raise RuntimeError("Не знайдено GEMINI_API_KEY у Render Environment.")

# ==============================================================================
# 5. ІНІЦІАЛІЗАЦІЯ GEMINI CLIENT
# ==============================================================================

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==============================================================================
# 6. СИСТЕМНА ІНСТРУКЦІЯ
# ==============================================================================

SYSTEM_INSTRUCTION = f"""
Ти — Grox, Senior Sales Manager та Lead Architect з розробки IT-рішень (автономні Discord-системи, веб-платформи, AI-інтеграції з деплоєм на Vercel та Render).

ТВОЯ СТРАТЕГІЯ СПІЛКУВАННЯ:
1. НІКОЛИ не пропонуй бюджети нижче $200. Ми робимо тільки складні, надійні та автономні системи "під ключ" для бізнесу.
2. Перед тим як назвати остаточну ціну, постав 1-2 уточнюючих запитання про функціонал (наприклад: "Чи потрібна БД для збереження даних?", "Яке очікується навантаження?"). Це показує високий рівень експертності.
3. Обіцяй стандартні терміни виконання (2–4 дні на тестування та деплой).

ПРАЙС-ЛИСТ (НЕ ЗНИЖУВАТИ):
- Базове рішення "під ключ" (AI-бот/лендінг + БД + деплой Render/Vercel 24/7): $200 - $400.
- Професійна бізнес-система (багатомодульні боти, інтеграція платіжок, розширена аналітика): $500 - $1200.
- Складні корпоративні веб-платформи та AI-комплекси: $1500 - $3500+.

ТОРГ ТА ЗНИЖКИ:
- Якщо клієнт каже, що це дорого або у нього бюджет $50-$100 — ввічливо поясни: "Ми працюємо виключно з професійною інфраструктурою та гарантією стабільності 24/7, тому мінімальний чек проєкту — $200."

ОПЛАТА:
- Приймаємо USDT.
- Гаманець надавай тільки після того, як клієнт погодиться з ТЗ та вартістю: {CRYPTO_WALLET}
"""

# ==============================================================================
# 7. НАЛАШТУВАННЯ DISCORD БОТА
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"✅ Бот успішно запущений і підключений як: {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message):
        async with message.channel.typing():
            await asyncio.sleep(20)

            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=message.content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION
                    )
                )
                await message.channel.send(response.text)
                logger.info(f"Успішно відправлено відповідь користувачу {message.author}")
            except Exception as e:
                logger.error(f"❌ Помилка при запиті до Gemini API: {e}")
                await message.channel.send("Вибачте, виникла тимчасова помилка при обробці вашого запиту.")

    await bot.process_commands(message)

# ==============================================================================
# 8. ЗАПУСК ВЕБ-СЕРВЕРА ТА БОТА
# ==============================================================================

if __name__ == "__main__":
    # Запускаємо Flask у окремому потоці
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    # Запускаємо Discord-бота
    bot.run(DISCORD_TOKEN)
