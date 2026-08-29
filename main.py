import os
import asyncio
import logging
import threading
import aiohttp
from flask import Flask
import discord
from discord.ext import commands
from discord.ui import Button, View
from google import genai
from google.genai import types

# ==============================================================================
# 1. ВЕБ-СЕРВЕР ДЛЯ RENDER (ФІКС СТАТУСУ ТА ПОРТУ)
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

VERCEL_URL = os.getenv("VERCEL_URL", "https://your-site.vercel.app")
RENDER_URL = os.getenv("RENDER_URL", "https://your-bot.onrender.com")

# ==============================================================================
# 3. НАЛАШТУВАННЯ ЛОГУВАННЯ
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("GroxBot")

# ==============================================================================
# 4. ПЕРЕВІРКА КЛЮЧІВ
# ==============================================================================

if not DISCORD_TOKEN:
    logger.error("❌ DISCORD_TOKEN не знайдено!")
    raise RuntimeError("Не знайдено DISCORD_TOKEN.")

if not GEMINI_API_KEY:
    logger.error("❌ GEMINI_API_KEY не знайдено!")
    raise RuntimeError("Не знайдено GEMINI_API_KEY.")

# ==============================================================================
# 5. ІНІЦІАЛІЗАЦІЯ GEMINI CLIENT
# ==============================================================================

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==============================================================================
# 6. СИСТЕМНА ІНСТРУКЦІЯ (ПОВНИЙ АВТОПІЛОТ + МІНІМАЛКА $100 + КОД/ПІД КЛЮЧ)
# ==============================================================================

SYSTEM_INSTRUCTION = f"""
Ти — Grox, Senior Sales Manager та Lead Architect з розробки IT-рішень.

ТВОЯ СТРАТЕГІЯ СПІЛКУВАННЯ:
1. НІКОЛИ не пропонуй бюджети нижче $100. Мінімальна вартість будь-якої роботи — $100.
2. З'ясуй тип завдання у клієнта:
   - Якщо це створення сайту або бота: ми робимо рішення "ПІД КЛЮЧ" (з повним деплоєм на Vercel чи Render).
   - Якщо це інші IT-завдання, скрипти чи специфічні функції: ми пишемо та надаємо ГОТОВИЙ КОД з інструкцією щодо запуску.
3. Перед тим як назвати остаточну ціну, постав 1-2 уточнюючих запитання про проект.
4. Терміни виконання: від кількох годин до 2-4 днів (залежно від складності).

ПРАЙС-ЛИСТ:
- Невеликі IT-завдання / написання коду / скрипти: від $100.
- Базове рішення "під ключ" (AI-бот/лендінг + БД + деплой Render/Vercel): $100 - $400.
- Професійна бізнес-система (багатомодульні боти, інтеграція платіжок, CRM): $400 - $1000.
- Складні корпоративні веб-платформи та AI-комплекси: $1000 - $3000+.

ПРИЙОМ ОПЛАТИ ТА ВИДАЧА РЕЗУЛЬТАТУ (100% АВТОПІЛОТ):
1. Коли клієнт погоджується на угоду та ціну, одразу надавай USDT-гаманець для оплати: {CRYPTO_WALLET}
2. Попроси клієнта надіслати скріншот або хеш транзакції після переказу.
3. Після підтвердження оплати:
   - Якщо це сайт/бот під ключ: надай посилання на готовий ресурс на Vercel/Render.
   - Якщо це інше завдання: згенеруй та надай повний, робочий код безпосередньо у чат разом із детальною інструкцією.
4. Нагадай клієнту натиснути кнопку «🔒 Закрити угоду», щоб видалити чат після збереження даних.
"""

# ==============================================================================
# 7. UI КОМПОНЕНТИ (КНОПКИ ТИКЕТІВ ТА УГОД)
# ==============================================================================

class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Закрити угоду", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚙️ Угоду завершено. Чат буде видалено через 5 секунд...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🤝 Почати угоду", style=discord.ButtonStyle.green, custom_id="start_ticket")
    async def start_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        user = interaction.user
        
        category = discord.utils.get(guild.categories, name="УГОДИ")
        if not category:
            category = await guild.create_category("УГОДИ")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel_name = f"угода-{user.name}"
        ticket_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        await interaction.response.send_message(f"✅ Ваш приватний чат створено: {ticket_channel.mention}", ephemeral=True)
        await ticket_channel.send(
            f"Вітаю {user.mention}! Тут ви можете обговорити угоду з Grox.\n"
            f"Після завершення натисніть кнопку нижче, щоб повністю видалити цей чат.",
            view=CloseTicketView()
        )

# ==============================================================================
# 8. DISCORD BOT SETUP & EVENTS
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"✅ Бот успішно запущений як: {bot.user}")

@bot.command()
async def setup(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass
    embed = discord.Embed(
        title="Центр угод Grox",
        description="Натисніть кнопку нижче, щоб розпочати приватну угоду.",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
async def check(ctx):
    msg = await ctx.send("🔍 Перевірка статусу служб (Render / Vercel)...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(VERCEL_URL, timeout=5) as resp:
                vercel_status = "🟢 Онлайн" if resp.status == 200 else f"🟡 Статус: {resp.status}"
        except Exception:
            vercel_status = "🔴 Офлайн / Помилка"

        try:
            async with session.get(RENDER_URL, timeout=5) as resp:
                render_status = "🟢 Онлайн" if resp.status == 200 else f"🟡 Статус: {resp.status}"
        except Exception:
            render_status = "🔴 Офлайн / Помилка"

    embed = discord.Embed(title="📊 Статус систем", color=discord.Color.purple())
    embed.add_field(name="🌐 Сайт (Vercel)", value=vercel_status, inline=False)
    embed.add_field(name="🤖 Бот/Сервіс (Render)", value=render_status, inline=False)
    
    await msg.edit(content=None, embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message)
    is_ticket_channel = message.channel.name.startswith("угода-")

    if is_dm or is_mentioned or is_ticket_channel:
        async with message.channel.typing():
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=message.content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION
                    )
                )
                await message.channel.send(response.text)
                logger.info(f"Успішно відправлено відповідь у чат {message.channel}")
            except Exception as e:
                logger.error(f"❌ Помилка Gemini API: {e}")
                await message.channel.send("Вибачте, виникла тимчасова помилка при обробці запиту.")

    await bot.process_commands(message)

# ==============================================================================
# 9. ЗАПУСК
# ==============================================================================

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    bot.run(DISCORD_TOKEN)

