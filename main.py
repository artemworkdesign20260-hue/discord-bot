import os
import asyncio
import logging
import discord
from discord.ext import commands
from google import genai
from google.genai import types

# ==============================================================================
# 1. НАЛАШТУВАННЯ ТА ЗМІННІ СЕРЕДОВИЩА
# ==============================================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CRYPTO_WALLET = os.getenv(
    "CRYPTO_WALLET",
    "адресу гаманця буде вказано під час оплати"
)

# Використовуємо актуальну та стабільну модель Gemini
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ==============================================================================
# 2. НАЛАШТУВАННЯ ЛОГУВАННЯ
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("GroxBot")

# ==============================================================================
# 3. ПЕРЕВІРКА НАЯВНОСТІ КЛЮЧІВ
# ==============================================================================

if not DISCORD_TOKEN:
    logger.error("❌ DISCORD_TOKEN не знайдено в змінних оточення!")
    raise RuntimeError("Не знайдено DISCORD_TOKEN у Render Environment.")

if not GEMINI_API_KEY:
    logger.error("❌ GEMINI_API_KEY не знайдено в змінних оточення!")
    raise RuntimeError("Не знайдено GEMINI_API_KEY у Render Environment.")

# ==============================================================================
# 4. ІНІЦІАЛІЗАЦІЯ GEMINI CLIENT
# ==============================================================================

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==============================================================================
# 5. СИСТЕМНА ІНСТРУКЦІЯ ДЛЯ МЕНЕДЖЕРА ПРОДАЖІВ
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
# 6. НАЛАШТУВАННЯ DISCORD БОТА
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"✅ Бот успішно запущений і підключений як: {bot.user}")

@bot.event
async def on_message(message):
    # Ігноруємо власні повідомлення бота
    if message.author == bot.user:
        return

    # Реагуємо на особисті повідомлення (DM) або згадки бота через @
    if isinstance(message.channel, discord.DMChannel) or bot.user.mentioned_in(message):
        async with message.channel.typing():
            # Затримка 20 секунд для імітації введення повідомлення людиною
            await asyncio.sleep(20)

            try:
                # Запит до Gemini API
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
# 7. ТОЧКА ВХОДУ (ЗАПУСК)
# ==============================================================================

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

