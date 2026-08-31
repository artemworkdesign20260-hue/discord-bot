import asyncio
import json
import os
import re
import aiohttp
import discord
from discord.ext import commands
import google.generativeai as genai

# ==========================================
# 1. ІНІЦІАЛІЗАЦІЯ ТА НАЛАШТУВАННЯ
# ==========================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")

# Налаштування Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-pro")

# Налаштування Discord
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Пошук будь-якого бюджету від $200 і вище (без обмеження зверху)
BUDGET_PATTERN = re.compile(
    r"(\$?\b([2-9]\d{2}|[1-9]\d{3,})\b|\b([2-9]\d{2}|[1-9]\d{3,})\$\b)"
)
KEYWORDS = ["bot", "бот", "site", "сайт", "web", "app", "скрипт", "розробка", "landing"]

# Анти-спам затримка (в секундах)
COOLDOWN_SECONDS = 180
last_processed_time = 0

# ==========================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ (VERCEL DEPLOY & AI)
# ==========================================

async def deploy_to_vercel(project_name: str, html_content: str) -> str:
    """Автоматичний деплой HTML-коду на Vercel через REST API."""
    url = "https://api.vercel.com/v13/deployments"
    headers = {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "name": project_name.lower().replace(" ", "-"),
        "files": [
            {
                "file": "index.html",
                "data": html_content
            }
        ],
        "projectSettings": {
            "framework": None
        }
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if resp.status in (200, 201):
                return f"https://{data.get('url')}"
            else:
                print(f"[VERCEL ERROR] {data}")
                return None

async def generate_site_code(prompt_text: str) -> str:
    """Генерація готового HTML/CSS/JS коду через Gemini."""
    ai_prompt = (
        "Ти професійний веб-розробник. Напиши повний, готовий до деплою єдиний HTML-файл "
        "(включаючи вбудовані CSS стилі та JS скрипти) за наступним ТЗ. "
        "Поверни ТІЛЬКИ чистий HTML код без додаткових пояснень чи markdown-блоків ```html.\n\n"
        f"ТЗ від клієнта: {prompt_text}"
    )
    response = model.generate_content(ai_prompt)
    clean_code = response.text.replace("```html", "").replace("```", "").strip()
    return clean_code

# ==========================================
# 3. ОСНОВНА ЛОГІКА БОТА
# ==========================================

@bot.event
async def on_ready():
    print(f"Grox автономно запущений! Акаунт: {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    global last_processed_time

    if message.author.bot:
        return

    text = message.content.lower()
    has_keyword = any(kw in text for kw in KEYWORDS)
    has_budget = bool(BUDGET_PATTERN.search(text))

    # Перевірка умов: Ключове слово + Бюджет від $200
    if has_keyword and has_budget:
        current_time = asyncio.get_event_loop().time()
        if current_time - last_processed_time < COOLDOWN_SECONDS:
            return  # Анти-спам захист

        last_processed_time = current_time
        print(f"[ЗНАЙДЕНО УГОДУ] від {message.author.name} у #{message.channel.name}")

        try:
            # Створення приватної гілки (Thread) для угоди
            thread = await message.create_thread(
                name=f"Угода $200+ | {message.author.name}",
                auto_archive_duration=60
            )

            await thread.send(
                f"Вітаю, {message.author.mention}! Я автономна система Grox.\n"
                f"Прийняв ваше замовлення у роботу. Напишіть сюди детальне ТЗ (що має бути на сайті/в ботові), "
                f"і я згенерую та задеплою готовий результат!"
            )

            # Чекаємо відповідь з ТЗ від клієнта
            def check(m):
                return m.author == message.author and m.channel == thread

            client_tz = await bot.wait_for("message", check=check, timeout=300.0)
            await thread.send("ТЗ отримано! Запускаю генерацію коду та авто-деплой на Vercel...")

            # Генерація та Деплой
            html_code = await generate_site_code(client_tz.content)
            project_slug = f"grox-job-{message.author.id}"
            live_url = await deploy_to_vercel(project_slug, html_code)

            if live_url:
                await thread.send(
                    f"**Угода виконана!**\n"
                    f"Ваш проект повністю готовий і задеплоєний:\n"
                    f"🔗 **Ссылка:** {live_url}\n\n"
                    f"Дякую за співпрацю!"
                )
            else:
                await thread.send("Виникла помилка під час деплою. Зверніться до адміністратора.")

        except asyncio.TimeoutError:
            print(f"[TIMEOUT] Клієнт {message.author.name} не надав ТЗ вчасно.")
        except Exception as e:
            print(f"[ERROR] {e}")

    await bot.process_commands(message)

# Запуск
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

