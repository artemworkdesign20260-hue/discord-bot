import asyncio
import os
import re
import uuid
import traceback
import random
from dataclasses import dataclass

import aiohttp
from aiohttp import web

import discord
from discord.ext import commands

from google import genai
from google.genai import types

from playwright.async_api import async_playwright



# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")

CLIENT_CHANNEL_ID = os.getenv("CLIENT_CHANNEL_ID")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.7-flash"
)

CLIENT_TIMEOUT = int(
    os.getenv("CLIENT_TIMEOUT", "600")
)

# How long Grox stays available for follow-up questions and revisions
# after the project is delivered. Discord threads auto-archive, so 24h
# is a practical default.
POST_DELIVERY_TIMEOUT = int(
    os.getenv("POST_DELIVERY_TIMEOUT", "86400")
)

# Maximum number of clarification rounds before Grox builds the final TЗ.
MAX_REQUIREMENT_ROUNDS = int(
    os.getenv("MAX_REQUIREMENT_ROUNDS", "10")
)

PORT = int(
    os.getenv("PORT", "10000")
)


# ============================================================
# SECURITY / PROJECT SETTINGS
# ============================================================

MAXIMUM_BUDGET = int(
    os.getenv("MAXIMUM_BUDGET", "10000")
)

MINIMUM_PROJECT_PRICE = 50

MAX_TASK_LENGTH = int(
    os.getenv("MAX_TASK_LENGTH", "12000")
)

GEMINI_RETRIES = int(
    os.getenv("GEMINI_RETRIES", "4")
)

MAX_WEBSITE_FIX_ATTEMPTS = 10

# How often the approval/payment state is checked.
STATE_CHECK_INTERVAL = 1


# ============================================================
# CHECK ENVIRONMENT
# ============================================================

required_variables = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "VERCEL_TOKEN": VERCEL_TOKEN,
    "CLIENT_CHANNEL_ID": CLIENT_CHANNEL_ID,
}

missing_variables = [
    name
    for name, value in required_variables.items()
    if not value
]

if missing_variables:
    raise RuntimeError(
        "Відсутні Environment Variables: "
        + ", ".join(missing_variables)
    )


try:
    CLIENT_CHANNEL_ID = int(CLIENT_CHANNEL_ID)
except ValueError:
    raise RuntimeError(
        "CLIENT_CHANNEL_ID повинен бути числом."
    )


# ============================================================
# GEMINI
# ============================================================

try:
    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    print(
        f"[GEMINI] Client initialized. "
        f"Model: {GEMINI_MODEL}"
    )

except Exception as error:
    print("========== GEMINI CLIENT ERROR ==========")
    print(f"Type: {type(error).__name__}")
    print(f"Message: {error!r}")
    traceback.print_exc()
    print("=========================================")
    raise


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# ORDER DATA
# ============================================================

@dataclass
class Order:
    order_id: int
    discord_message_id: int
    client_id: int
    client_name: str

    budget: int = 0

    thread_id: int = 0

    task: str = ""
    project_type: str = ""
    complexity: str = ""
    price_reason: str = ""

    client_budget: int | None = None
    client_approved_price: bool = False

    site_url: str | None = None
    site_code: str | None = None

    status: str = "WAITING_TZ"

    # Used to prevent stale/duplicate approval handling.
    approval_message_id: int = 0


orders: dict[int, Order] = {}
active_orders: set[int] = set()

next_order_id = 1000


# ============================================================
# ORDER HELPERS
# ============================================================

def create_order(message: discord.Message):
    global next_order_id

    next_order_id += 1

    order = Order(
        order_id=next_order_id,
        discord_message_id=message.id,
        client_id=message.author.id,
        client_name=message.author.name,
    )

    orders[order.order_id] = order

    print(
        f"[ORDER CREATED] #{order.order_id} "
        f"client={order.client_id} "
        f"message={message.id}"
    )

    return order


def get_order_thread(order: Order):
    if not order.thread_id:
        return None

    channel = bot.get_channel(order.thread_id)

    if isinstance(channel, discord.Thread):
        return channel

    return None


def normalize_user_text(text: str) -> str:
    """
    Normalizes client messages so approval works even when:
    - capitalization differs;
    - there are extra spaces;
    - Discord/mobile adds line breaks;
    - the user sends punctuation;
    - the user repeats the approval phrase.
    """
    text = text.casefold().strip()

    text = text.replace("’", "'")
    text = text.replace("`", "")
    text = re.sub(r"[^\w\s'а-яіїєґёa-z0-9-]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def is_price_approval(text: str) -> bool:
    """
    Accepts natural variants instead of requiring the entire
    message to equal exactly one word.
    """
    normalized = normalize_user_text(text)

    approval_phrases = (
        "погоджуюсь",
        "погоджуюся",
        "погоджуюсь!",
        "погоджуюся!",
        "згоден",
        "згодна",
        "підтверджую",
        "підтверджую!",
        "agree",
        "approved",
        "approve",
        "yes",
        "ok",
        "okay",
        "ок",
        "так",
    )

    if normalized in approval_phrases:
        return True

    # Also accept messages such as:
    # "Погоджуюсь, робимо"
    # "Так, погоджуюсь"
    # "Погоджуюсь 👍"
    words = set(normalized.split())

    if "погоджуюсь" in words:
        return True

    if "погоджуюся" in words:
        return True

    if "підтверджую" in words:
        return True

    if normalized.startswith("погоджуюсь "):
        return True

    if normalized.startswith("погоджуюся "):
        return True

    return False


def is_new_task_message(text: str) -> bool:
    """
    A message in the price-approval stage that is not approval
    is treated as a new/changed task rather than silently ignored.
    """
    normalized = normalize_user_text(text)

    if not normalized:
        return False

    return not is_price_approval(normalized)


# ============================================================
# ORDER STATUS MESSAGES
# ============================================================

def price_status_message(order: Order):
    client_budget_text = ""

    if order.client_budget is not None:
        client_budget_text = (
            f"📌 Ваш орієнтовний бюджет: "
            f"**${order.client_budget}**\n\n"
        )

    return (
        f"🟡 **Замовлення #{order.order_id}**\n\n"
        f"🛠️ Тип проєкту: "
        f"**{order.project_type}**\n"
        f"📊 Складність: "
        f"**{order.complexity}**\n\n"
        f"{client_budget_text}"
        f"💰 **Запропонована вартість: "
        f"${order.budget}**\n\n"
        f"📝 **Чому така ціна:**\n"
        f"{order.price_reason}\n\n"
        f"⏳ Очікую погодження ціни."
    )








def completed_message(order: Order):
    return (
        f"🟢 **Замовлення #{order.order_id} завершене!**\n\n"
        f"💰 Вартість проєкту: **${order.budget}**\n"
        f"✅ Проєкт виконано."
    )


# ============================================================
# ORDER DETECTION
# ============================================================

KEYWORDS = [
    "bot",
    "бот",
    "bots",
    "бота",
    "ботом",

    "site",
    "сайт",
    "website",
    "web",
    "веб",

    "app",
    "апка",
    "додаток",

    "script",
    "скрипт",

    "development",
    "розробка",

    "landing",
    "лендинг",

    "discord bot",
    "discord бот",
]


def contains_service_keyword(text: str) -> bool:
    text = text.casefold()

    return any(
        keyword in text
        for keyword in KEYWORDS
    )


def is_order_message(text: str) -> bool:
    return contains_service_keyword(text)


# ============================================================
# OPTIONAL CLIENT BUDGET
# ============================================================

BUDGET_PATTERN = re.compile(
    r"""
    (?:
        \$\s*(\d[\d\s,\.]*)
        |
        (\d[\d\s,\.]*)\s*\$
        |
        (\d[\d\s,\.]*)\s*
        (?:usd|dollars?|долар(?:ів|и)?)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def extract_budget(text: str):
    match = BUDGET_PATTERN.search(text)

    if not match:
        return None

    for group in match.groups():
        if group:
            try:
                cleaned = (
                    group
                    .replace(" ", "")
                    .replace(",", "")
                    .replace(".", "")
                )

                return int(cleaned)

            except ValueError:
                return None

    return None


# ============================================================
# PAYMENT TEST SYSTEM
# ============================================================



# ============================================================
# ADMIN PAYMENT CONFIRMATION
# ============================================================



# ============================================================
# GEMINI REQUEST
# ============================================================

async def gemini_request(
    prompt: str,
    max_output_tokens: int
):
    last_error = None

    for attempt in range(
        1,
        GEMINI_RETRIES + 1
    ):
        try:
            print(
                f"[GEMINI] Attempt "
                f"{attempt}/{GEMINI_RETRIES}"
            )

            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_output_tokens
                )
            )

            if response is None:
                raise RuntimeError(
                    "Gemini повернув None."
                )

            response_text = response.text

            if not response_text:
                raise RuntimeError(
                    "Gemini повернув порожню відповідь."
                )

            return response_text.strip()

        except Exception as error:
            last_error = error

            print(
                f"[GEMINI ERROR] "
                f"Attempt {attempt}: "
                f"{type(error).__name__}: "
                f"{error!r}"
            )

            error_text = str(error).lower()

            temporary = any(
                code in error_text
                for code in (
                    "503",
                    "unavailable",
                    "temporarily",
                    "deadline",
                    "timeout",
                    "429",
                    "rate limit"
                )
            )

            if not temporary:
                traceback.print_exc()
                break

            if attempt >= GEMINI_RETRIES:
                break

            delay = (
                2 ** attempt
            ) + random.uniform(0, 1)

            print(
                f"[GEMINI] Повтор через "
                f"{delay:.1f}s..."
            )

            await asyncio.sleep(delay)

    if last_error:
        raise last_error

    raise RuntimeError(
        "Gemini не зміг виконати запит."
    )



# ============================================================
# REQUIREMENT DISCOVERY / CLIENT CONVERSATION
# ============================================================

async def analyze_requirements(conversation: str):
    prompt = f"""
Ти — Grox, AI-менеджер IT-проєктів.

Ти зараз ведеш розмову з клієнтом ПЕРЕД початком роботи.
Твоя головна мета — повністю зрозуміти, що саме клієнт хоче отримати.

РОЗМОВА З КЛІЄНТОМ:
{conversation}

Виріши, чи вже достатньо інформації для виконання проєкту.

Якщо важливої інформації не вистачає:
- постав ОДНЕ найважливіше уточнювальне питання;
- не став кілька питань одним повідомленням;
- не повторюй те, що клієнт уже сказав;
- говори просто, природно і по-людськи;
- якщо доречно, запропонуй клієнту кілька зрозумілих варіантів.

Якщо інформації достатньо:
- сформуй повне фінальне ТЗ;
- використовуй ТІЛЬКИ інформацію з розмови;
- не вигадуй функцій, дизайну, текстів або вимог;
- об'єднай усі побажання клієнта в одне чітке ТЗ.

Поверни РІВНО такий формат:

READY: YES або NO
QUESTION: ...
FINAL_TZ: ...

Якщо READY: NO — FINAL_TZ залиш порожнім.
Якщо READY: YES — QUESTION залиш порожнім.
"""

    result = await gemini_request(
        prompt,
        max_output_tokens=3000
    )

    ready_match = re.search(
        r"READY\s*:\s*(YES|NO)",
        result,
        re.IGNORECASE
    )

    question_match = re.search(
        r"QUESTION\s*:\s*(.*?)(?=\nFINAL_TZ\s*:|\Z)",
        result,
        re.IGNORECASE | re.DOTALL
    )

    final_match = re.search(
        r"FINAL_TZ\s*:\s*(.*)\Z",
        result,
        re.IGNORECASE | re.DOTALL
    )

    return {
        "ready": bool(
            ready_match
            and ready_match.group(1).upper() == "YES"
        ),
        "question": (
            question_match.group(1).strip()
            if question_match
            else ""
        ),
        "final_tz": (
            final_match.group(1).strip()
            if final_match
            else ""
        ),
    }


async def discover_requirements(
    order: Order,
    thread: discord.Thread,
    first_message: str
):
    conversation = (
        f"Клієнт: {first_message.strip()}"
    )

    await thread.send(
        "🧠 **Добре, спочатку розберімося із завданням.**\n\n"
        "Я поставлю кілька коротких уточнень, якщо вони будуть потрібні. "
        "Після цього сформую фінальне ТЗ і покажу вам ціну."
    )

    for round_number in range(1, MAX_REQUIREMENT_ROUNDS + 1):
        analysis = await analyze_requirements(
            conversation
        )

        if analysis["ready"] and analysis["final_tz"]:
            return analysis["final_tz"]

        question = analysis["question"]

        if not question:
            question = (
                "Що саме має робити цей проєкт і який результат "
                "ви хочете отримати?"
            )

        await thread.send(
            f"🤖 **Уточнення {round_number}/{MAX_REQUIREMENT_ROUNDS}:**\n\n"
            f"{question}"
        )

        def check(msg: discord.Message):
            return (
                msg.author.id == order.client_id
                and msg.channel.id == order.thread_id
                and not msg.author.bot
            )

        try:
            reply = await bot.wait_for(
                "message",
                check=check,
                timeout=CLIENT_TIMEOUT
            )
        except asyncio.TimeoutError:
            await thread.send(
                "⏰ Час очікування відповіді минув.\n\n"
                "Якщо хочете продовжити — створіть нове замовлення."
            )
            return None

        answer = reply.content.strip()

        if not answer:
            await thread.send(
                "❌ Будь ласка, напишіть відповідь текстом."
            )
            continue

        if len(answer) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ Повідомлення занадто велике.\n"
                f"Максимум: {MAX_TASK_LENGTH} символів."
            )
            continue

        conversation += (
            f"\n\nКлієнт: {answer}"
        )

        await thread.send(
            "✅ **Зрозумів.**"
        )

    # After the maximum number of rounds, ask Gemini to create the
    # best TЗ from information the client actually provided.
    final_analysis = await analyze_requirements(
        conversation
    )

    if final_analysis["final_tz"]:
        return final_analysis["final_tz"]

    return conversation


# ============================================================
# PROJECT PRICE ESTIMATION
# ============================================================

async def estimate_project(client_task: str):
    prompt = f"""
Ти — професійний менеджер IT-проєктів
системи Grox.

Проаналізуй технічне завдання клієнта
та визнач справедливу вартість роботи.

Ціна повинна залежати від реальної
складності проєкту.

ОЦІНЮЙ:

- кількість функцій;
- складність функцій;
- дизайн;
- frontend;
- backend;
- базу даних;
- авторизацію;
- API;
- інтеграції;
- платежі;
- адміністративну панель;
- автоматизацію;
- Discord-функції;
- приблизний обсяг програмування;
- необхідність тестування.

РІВНІ:

VERY_SIMPLE = $50–$150
SIMPLE = $150–$300
MEDIUM = $300–$700
HARD = $700–$1500
VERY_HARD = $1500–$3000+

ВАЖЛИВІ ПРАВИЛА:

1. Не роби автоматично ціну $500.
2. Не використовуй бюджет клієнта як автоматичну ціну.
3. Якщо клієнт не вказав бюджет — це нормально.
4. Якщо клієнт вказав бюджет — порівняй його
   з реальною оцінкою.
5. Не вигадуй функції, яких немає в ТЗ.
6. Ціна повинна відповідати складності.
7. Мінімальна ціна: ${MINIMUM_PROJECT_PRICE}.
8. Максимальна ціна: ${MAXIMUM_BUDGET}.

ПОВЕРНИ РІВНО ТАКИЙ ФОРМАТ:

TYPE: ...
COMPLEXITY: ...
PRICE: ...
REASON: ...

ТЕХНІЧНЕ ЗАВДАННЯ:

{client_task}
"""

    result = await gemini_request(
        prompt,
        max_output_tokens=3000
    )

    price_match = re.search(
        r"PRICE\s*:\s*\$?\s*(\d+)",
        result,
        re.IGNORECASE
    )

    if not price_match:
        raise RuntimeError(
            "Gemini не повернув коректну ціну."
        )

    price = int(
        price_match.group(1)
    )

    price = max(
        MINIMUM_PROJECT_PRICE,
        price
    )

    price = min(
        MAXIMUM_BUDGET,
        price
    )

    type_match = re.search(
        r"TYPE\s*:\s*(.+)",
        result,
        re.IGNORECASE
    )

    complexity_match = re.search(
        r"COMPLEXITY\s*:\s*(.+)",
        result,
        re.IGNORECASE
    )

    reason_match = re.search(
        r"REASON\s*:\s*(.+)",
        result,
        re.IGNORECASE
    )

    project_type = (
        type_match.group(1).strip()
        if type_match
        else "IT Project"
    )

    complexity = (
        complexity_match.group(1).strip()
        if complexity_match
        else "UNKNOWN"
    )

    reason = (
        reason_match.group(1).strip()
        if reason_match
        else "Ціна визначена на основі складності ТЗ."
    )

    return {
        "price": price,
        "type": project_type,
        "complexity": complexity,
        "reason": reason,
        "analysis": result,
    }


# ============================================================
# GEMINI WEBSITE GENERATION
# ============================================================

async def generate_site_code(client_task: str) -> str:
    if not client_task.strip():
        raise ValueError(
            "Порожнє технічне завдання."
        )

    if len(client_task) > MAX_TASK_LENGTH:
        raise ValueError(
            "Технічне завдання занадто велике."
        )

    prompt = f"""
Ти — професійний веб-розробник системи Grox.

Створи повністю готовий до запуску
односторінковий вебсайт за технічним
завданням клієнта.

КРИТИЧНО ВАЖЛИВО:

ЦЕ НЕ МАКЕТ І НЕ ФОТО.

УСІ ФУНКЦІЇ, ЯКІ ВКАЗАНІ В ТЗ,
ПОВИННІ РЕАЛЬНО ПРАЦЮВАТИ.

Кнопки не повинні бути декоративними,
якщо за ТЗ вони повинні виконувати дію.

Форми повинні мати реальну поведінку.

JavaScript повинен бути робочим.

Не створюй кнопку, яка нічого не робить,
якщо клієнт очікує функціональність.

ПРАВИЛА:

1. Поверни ТІЛЬКИ HTML-код.
2. Не використовуй Markdown.
3. Не використовуй ```html.
4. CSS всередині HTML.
5. JavaScript всередині HTML.
6. Сайт адаптивний.
7. Сучасний UI/UX.
8. Якщо клієнт не вказав кольори —
   вибери професійну схему.
9. Не додавай пояснення.
10. Один повний index.html.
11. Не залишай TODO.
12. Не залишай фальшиві кнопки.
13. Не залишай фальшиві форми.
14. Не залишай очевидно незавершені функції.
15. HTML має DOCTYPE, html, head та body.
16. Не вигадуй реальних клієнтів,
    компаній, відгуків або результатів.
17. Не використовуй фальшиві testimonials
    від імені реальних людей.
18. Якщо потрібен backend/API, а ТЗ його
    вимагає, не вдавай, що frontend сам
    по собі є backend.
19. Якщо функцію неможливо реалізувати
    тільки frontend-ом, реалізуй безпечну
    демонстраційну поведінку або чітко
    врахуй необхідність backend у коді.
20. Перед відповіддю сам перевір логіку
    JavaScript та взаємодію елементів.

Технічне завдання:

{client_task}
"""

    return await gemini_request(
        prompt,
        max_output_tokens=30000
    )


# ============================================================
# GEMINI WEBSITE FIX
# ============================================================

async def fix_site_code(
    html_code: str,
    test_report: str,
    client_task: str
) -> str:
    prompt = f"""
Ти — senior frontend developer системи Grox.

Тобі потрібно ВИПРАВИТИ існуючий HTML-сайт.

Клієнтське ТЗ:

{client_task}

РЕЗУЛЬТАТ АВТОМАТИЧНОЇ ПЕРЕВІРКИ:

{test_report}

ВИМОГИ:

1. Виправ усі знайдені проблеми.
2. Не прибирай функції, які потрібні за ТЗ.
3. Не замінюй функціональність картинкою
   або декоративним елементом.
4. Кнопки повинні виконувати свої дії.
5. JavaScript повинен працювати.
6. Форми повинні працювати відповідно до ТЗ.
7. Не залишай TODO.
8. Не додавай Markdown.
9. Поверни ТІЛЬКИ повний готовий HTML.
10. CSS всередині HTML.
11. JavaScript всередині HTML.
12. Не пояснюй зміни.
13. Збережи професійний дизайн.
14. Не вигадуй нові функції, яких немає
    в ТЗ, якщо вони не потрібні для виправлення.

ПОТОЧНИЙ HTML:

{html_code}
"""

    return await gemini_request(
        prompt,
        max_output_tokens=30000
    )



# ============================================================
# CLIENT CONVERSATION + WEBSITE REVISIONS
# ============================================================

def is_revision_request(text: str) -> bool:
    normalized = normalize_user_text(text)

    revision_words = (
        "правк", "змін", "додай", "добав", "прибери", "видали",
        "зроби", "перероби", "виправ", "заміни", "хочу щоб",
        "хочу, щоб", "потрібно змінити", "треба змінити",
        "не подобається", "колір", "кнопк", "текст", "дизайн",
        "секц", "блок", "додати", "прибрати",
    )

    return any(word in normalized for word in revision_words)


async def revise_site_code(
    html_code: str,
    current_task: str,
    client_revision: str
) -> str:
    prompt = f"""
Ти — senior frontend developer системи Grox.

Клієнт уже отримав готовий сайт і зараз просить правку.

ПОТОЧНЕ ТЗ ПРОЄКТУ:
{current_task}

НОВА ПРАВКА КЛІЄНТА:
{client_revision}

ПОТОЧНИЙ HTML:
{html_code}

ЗАВДАННЯ:
1. Зрозумій, що саме хоче змінити клієнт.
2. Внеси цю правку в існуючий сайт.
3. Збережи всі вже працюючі функції.
4. Не прибирай наявні секції або функції без причини.
5. Не вигадуй зміни, яких клієнт не просив.
6. Якщо клієнт просить дизайн — зміни дизайн, не ламаючи функціональність.
7. Якщо клієнт просить кнопку, форму або поведінку — зроби її реально робочою.
8. Поверни ТІЛЬКИ повний HTML.
9. CSS і JavaScript мають залишатися всередині HTML.
10. Не використовуй Markdown або ```html.
11. Перед відповіддю перевір логіку JavaScript.

ПОВЕРНИ ПОВНУ ОНОВЛЕНУ ВЕРСІЮ САЙТУ.
"""

    return await gemini_request(
        prompt,
        max_output_tokens=30000
    )


async def revise_and_test_website(
    order: Order,
    thread: discord.Thread,
    html_code: str
):
    for attempt in range(
        1,
        MAX_WEBSITE_FIX_ATTEMPTS + 1
    ):
        await thread.send(
            f"🧪 **Перевіряю оновлений сайт "
            f"{attempt}/{MAX_WEBSITE_FIX_ATTEMPTS}...**\n\n"
            "🔍 Перевіряю завантаження, JavaScript, "
            "кнопки, посилання та форми."
        )

        project_name = (
            f"grox-revision-"
            f"{uuid.uuid4().hex[:12]}"
        )

        live_url = await deploy_to_vercel(
            project_name,
            html_code
        )

        if not live_url:
            if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
                return False
            await thread.send(
                "⚠️ Оновлений сайт не вдалося розгорнути. "
                "Повторюю спробу."
            )
            continue

        qa_result = await test_website(live_url)

        if qa_result["success"]:
            order.site_url = live_url
            order.site_code = html_code
            await thread.send(
                "✅ **Правку внесено та перевірено!**\n\n"
                f"🌐 **Оновлений сайт:**\n{live_url}"
            )
            return True

        await thread.send(
            f"❌ **Після правки QA знайшов проблему.**\n\n"
            f"🔧 Grox автоматично виправляє її.\n"
            f"Спроба: **{attempt}/{MAX_WEBSITE_FIX_ATTEMPTS}**"
        )

        if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
            await thread.send(
                "❌ **Не вдалося стабільно завершити правку "
                "після максимальної кількості спроб.**\n\n"
                "Я не передаю цей варіант як готовий."
            )
            return False

        try:
            html_code = await fix_site_code(
                html_code,
                qa_result["report"],
                order.task
            )
        except Exception as error:
            print(
                f"[REVISION FIX ERROR] "
                f"{type(error).__name__}: {error!r}"
            )
            traceback.print_exc()
            await thread.send(
                "❌ Не вдалося автоматично виправити "
                "сайт після правки."
            )
            return False

    return False


async def answer_client_message(
    order: Order,
    client_message: str
):
    prompt = f"""
Ти — Grox, AI-менеджер і розробник IT-проєктів.

Ти спілкуєшся з клієнтом у приватному Discord thread.

Поточне ТЗ:
{order.task}

Тип проєкту:
{order.project_type}

Поточна погоджена ціна:
${order.budget}

Повідомлення клієнта:
{client_message}

Відповідай українською, коротко й по суті.
Ти можеш:
- відповісти на питання про проєкт;
- пояснити, що вже зроблено;
- обговорити запропоновану правку;
- попросити уточнення, якщо правка незрозуміла;
- підтвердити, що зрозумів побажання.

Не вигадуй, що робота вже виконана, якщо цього немає в контексті.
Не називай нову ціну без достатньої інформації.
"""

    return await gemini_request(
        prompt,
        max_output_tokens=1500
    )


async def post_delivery_conversation(
    order: Order,
    thread: discord.Thread
):
    await thread.send(
        "💬 **Grox залишається на зв'язку.**\n\n"
        "Можете поставити питання або написати, що потрібно змінити "
        "у проєкті. Якщо це правка сайту — я внесу її, перевірю "
        "результат і надішлю оновлену версію."
    )

    started_at = asyncio.get_running_loop().time()

    while True:
        elapsed = (
            asyncio.get_running_loop().time()
            - started_at
        )

        remaining_timeout = max(
            1,
            POST_DELIVERY_TIMEOUT - int(elapsed)
        )

        def check(msg: discord.Message):
            return (
                msg.author.id == order.client_id
                and msg.channel.id == order.thread_id
                and not msg.author.bot
            )

        try:
            client_message = await bot.wait_for(
                "message",
                check=check,
                timeout=remaining_timeout
            )
        except asyncio.TimeoutError:
            return

        text = client_message.content.strip()

        if not text:
            continue

        if len(text) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ Повідомлення занадто велике. "
                f"Максимум: {MAX_TASK_LENGTH} символів."
            )
            continue

        print(
            f"[CLIENT FOLLOW-UP] "
            f"order=#{order.order_id} "
            f"content={text!r}"
        )

        if order.site_code and is_revision_request(text):
            await thread.send(
                "🛠️ **Зрозумів правку.**\n\n"
                "🧠 Аналізую, що саме потрібно змінити, "
                "і внесу це в поточну версію сайту."
            )

            try:
                updated_task = (
                    order.task
                    + "\n\n"
                    + "Додаткова правка клієнта: "
                    + text
                )

                revised_html = await revise_site_code(
                    order.site_code,
                    updated_task,
                    text
                )

                order.task = updated_task

                success = await revise_and_test_website(
                    order,
                    thread,
                    revised_html
                )

                if not success:
                    await thread.send(
                        "⚠️ Правку я зрозумів, але не буду "
                        "вважати її готовою, поки сайт не пройде "
                        "перевірку."
                    )

            except Exception as error:
                print(
                    f"[CLIENT REVISION ERROR] "
                    f"{type(error).__name__}: {error!r}"
                )
                traceback.print_exc()
                await thread.send(
                    "❌ Сталася технічна помилка під час внесення правки. "
                    "Можете описати її ще раз."
                )

            started_at = asyncio.get_running_loop().time()
            continue

        try:
            reply = await answer_client_message(
                order,
                text
            )
            await thread.send(f"🤖 {reply}")

        except Exception as error:
            print(
                f"[CLIENT CHAT ERROR] "
                f"{type(error).__name__}: {error!r}"
            )
            traceback.print_exc()
            await thread.send(
                "🤖 Я тут. Уточніть, будь ласка, "
                "що саме потрібно зробити або змінити."
            )

        started_at = asyncio.get_running_loop().time()


# ============================================================
# VERCEL DEPLOY
# ============================================================

async def deploy_to_vercel(
    project_name: str,
    html_content: str
):
    url = (
        "https://api.vercel.com/v13/deployments"
    )

    headers = {
        "Authorization":
            f"Bearer {VERCEL_TOKEN}",
        "Content-Type":
            "application/json",
    }

    payload = {
        "name":
            project_name,
        "files": [
            {
                "file":
                    "index.html",
                "data":
                    html_content,
            }
        ],
        "projectSettings": {
            "framework":
                None
        }
    }

    timeout = aiohttp.ClientTimeout(
        total=120
    )

    print(
        f"[VERCEL] Starting deployment: "
        f"{project_name}"
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            async with session.post(
                url,
                headers=headers,
                json=payload
            ) as response:
                response_text = (
                    await response.text()
                )

                try:
                    data = await response.json(
                        content_type=None
                    )
                except Exception:
                    data = {}

                if response.status in (
                    200,
                    201
                ):
                    deployment_url = data.get(
                        "url"
                    )

                    if deployment_url:
                        if not deployment_url.startswith(
                            "http"
                        ):
                            deployment_url = (
                                "https://"
                                + deployment_url
                            )

                        print(
                            f"[VERCEL] "
                            f"Deployment successful: "
                            f"{deployment_url}"
                        )

                        return deployment_url

                print(
                    f"[VERCEL ERROR] "
                    f"HTTP {response.status}: "
                    f"{response_text}"
                )

                return None

    except Exception as error:
        print(
            f"[VERCEL EXCEPTION] "
            f"{type(error).__name__}: "
            f"{error!r}"
        )

        traceback.print_exc()

        return None


# ============================================================
# WEBSITE QA TEST
# ============================================================

async def test_website(url: str):
    print(
        f"[QA] Starting website test: {url}"
    )

    errors = []
    warnings = []
    console_errors = []
    failed_requests = []
    clicked_elements = 0

    title = ""
    button_count = 0
    link_count = 0
    form_count = 0

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True
            )

            page = await browser.new_page()

            # ------------------------------------------------
            # JavaScript console errors
            # ------------------------------------------------

            def handle_console(msg):
                if msg.type == "error":
                    console_errors.append(
                        msg.text
                    )

            page.on(
                "console",
                handle_console
            )

            # ------------------------------------------------
            # Failed network requests
            # ------------------------------------------------

            def handle_request_failed(request):
                failed_requests.append(
                    f"{request.method} {request.url} "
                    f"-> {request.failure}"
                )

            page.on(
                "requestfailed",
                handle_request_failed
            )

            # ------------------------------------------------
            # PAGE ERROR
            # ------------------------------------------------

            def handle_page_error(error):
                errors.append(
                    f"JavaScript page error: {error}"
                )

            page.on(
                "pageerror",
                handle_page_error
            )

            # ------------------------------------------------
            # OPEN WEBSITE
            # ------------------------------------------------

            try:
                response = await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=30000
                )

            except Exception as error:
                await browser.close()

                return {
                    "success": False,
                    "report": (
                        "Сайт не вдалося відкрити.\n"
                        f"Помилка: {error}"
                    )
                }

            if response is None:
                errors.append(
                    "Сторінка не повернула HTTP response."
                )

            elif response.status >= 400:
                errors.append(
                    f"HTTP помилка: {response.status}"
                )

            # ------------------------------------------------
            # BASIC HTML CHECK
            # ------------------------------------------------

            title = await page.title()

            html = await page.locator(
                "html"
            ).count()

            body = await page.locator(
                "body"
            ).count()

            if html == 0:
                errors.append(
                    "Відсутній <html>."
                )

            if body == 0:
                errors.append(
                    "Відсутній <body>."
                )

            # ------------------------------------------------
            # JAVASCRIPT ERRORS
            # ------------------------------------------------

            for console_error in console_errors:
                errors.append(
                    f"Console error: {console_error}"
                )

            # ------------------------------------------------
            # FAILED REQUESTS
            # ------------------------------------------------

            for failed_request in failed_requests:
                warnings.append(
                    f"Неуспішний network request: "
                    f"{failed_request}"
                )

            # ------------------------------------------------
            # FIND BUTTONS
            # ------------------------------------------------

            buttons = page.locator(
                "button"
            )

            button_count = await buttons.count()

            print(
                f"[QA] Buttons found: "
                f"{button_count}"
            )

            # ------------------------------------------------
            # TEST BUTTONS
            # ------------------------------------------------

            for index in range(
                min(button_count, 30)
            ):
                try:
                    button = buttons.nth(
                        index
                    )

                    if not await button.is_visible():
                        continue

                    disabled = await button.is_disabled()

                    if disabled:
                        continue

                    before_url = page.url

                    before_text = await page.locator(
                        "body"
                    ).inner_text(
                        timeout=3000
                    )

                    await button.scroll_into_view_if_needed()

                    await button.click(
                        timeout=5000
                    )

                    clicked_elements += 1

                    await page.wait_for_timeout(
                        500
                    )

                    after_text = await page.locator(
                        "body"
                    ).inner_text(
                        timeout=3000
                    )

                    after_url = page.url

                    if (
                        before_url == after_url
                        and before_text == after_text
                    ):
                        warnings.append(
                            f"Кнопка #{index + 1} "
                            f"не показала очевидної зміни "
                            f"після натискання."
                        )

                    if before_url != after_url:
                        try:
                            await page.go_back(
                                wait_until="networkidle",
                                timeout=10000
                            )
                        except Exception:
                            pass

                except Exception as error:
                    errors.append(
                        f"Кнопка #{index + 1} "
                        f"спричинила помилку: {error}"
                    )

            # ------------------------------------------------
            # TEST LINKS
            # ------------------------------------------------

            links = page.locator(
                "a"
            )

            link_count = await links.count()

            print(
                f"[QA] Links found: "
                f"{link_count}"
            )

            for index in range(
                min(link_count, 30)
            ):
                try:
                    link = links.nth(
                        index
                    )

                    if not await link.is_visible():
                        continue

                    href = await link.get_attribute(
                        "href"
                    )

                    if not href:
                        warnings.append(
                            f"Посилання #{index + 1} "
                            f"не має href."
                        )

                except Exception as error:
                    warnings.append(
                        f"Не вдалося перевірити "
                        f"посилання #{index + 1}: "
                        f"{error}"
                    )

            # ------------------------------------------------
            # TEST FORMS
            # ------------------------------------------------

            forms = page.locator(
                "form"
            )

            form_count = await forms.count()

            print(
                f"[QA] Forms found: "
                f"{form_count}"
            )

            for index in range(
                form_count
            ):
                try:
                    form = forms.nth(
                        index
                    )

                    inputs = form.locator(
                        "input"
                    )

                    input_count = await inputs.count()

                    if input_count == 0:
                        warnings.append(
                            f"Форма #{index + 1} "
                            f"не має input."
                        )

                except Exception as error:
                    errors.append(
                        f"Помилка перевірки "
                        f"форми #{index + 1}: "
                        f"{error}"
                    )

            await browser.close()

    except Exception as error:
        traceback.print_exc()

        return {
            "success": False,
            "report": (
                "QA-система не змогла завершити "
                "перевірку.\n"
                f"Помилка: {type(error).__name__}: "
                f"{error}"
            )
        }

    # ========================================================
    # RESULT
    # ========================================================

    success = (
        len(errors) == 0
    )

    report_lines = [
        f"URL: {url}",
        f"Title: {title}",
        f"Кнопок знайдено: {button_count}",
        f"Кнопок натиснуто: {clicked_elements}",
        f"Посилань знайдено: {link_count}",
        f"Форм знайдено: {form_count}",
    ]

    if errors:
        report_lines.append(
            "\n❌ ПОМИЛКИ:"
        )

        for error in errors[:50]:
            report_lines.append(
                f"- {error}"
            )

    if warnings:
        report_lines.append(
            "\n⚠️ ПОПЕРЕДЖЕННЯ:"
        )

        for warning in warnings[:50]:
            report_lines.append(
                f"- {warning}"
            )

    if success:
        report_lines.append(
            "\n✅ Критичних помилок не знайдено."
        )
    else:
        report_lines.append(
            "\n❌ Сайт НЕ пройшов QA."
        )

    report = "\n".join(
        report_lines
    )

    print(
        "[QA RESULT]"
    )

    print(
        report
    )

    return {
        "success": success,
        "report": report,
        "errors": errors,
        "warnings": warnings,
    }


# ============================================================
# WEBSITE GENERATE + TEST + FIX
# ============================================================

async def generate_test_and_fix_website(
    order: Order,
    thread: discord.Thread
):
    # --------------------------------------------------------
    # FIRST GENERATION
    # --------------------------------------------------------

    await thread.send(
        "💻 **Генерую сайт...**"
    )

    html_code = await generate_site_code(
        order.task
    )

    # --------------------------------------------------------
    # AUTOMATIC QA LOOP
    # --------------------------------------------------------

    for attempt in range(
        1,
        MAX_WEBSITE_FIX_ATTEMPTS + 1
    ):
        await thread.send(
            f"🧪 **Перевірка сайту "
            f"{attempt}/{MAX_WEBSITE_FIX_ATTEMPTS}...**\n\n"
            "🔍 Перевіряю завантаження, "
            "JavaScript, кнопки, посилання "
            "та форми."
        )

        # ----------------------------------------------------
        # DEPLOY TEST VERSION
        # ----------------------------------------------------

        project_name = (
            f"grox-job-"
            f"{uuid.uuid4().hex[:12]}"
        )

        live_url = await deploy_to_vercel(
            project_name,
            html_code
        )

        if not live_url:
            if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
                raise RuntimeError(
                    "Vercel не зміг виконати "
                    "тестовий деплой."
                )

            await thread.send(
                "⚠️ Тестовий деплой не вдався. "
                "Повторюю."
            )

            continue

        # ----------------------------------------------------
        # RUN QA
        # ----------------------------------------------------

        qa_result = await test_website(
            live_url
        )

        if qa_result["success"]:
            await thread.send(
                "✅ **QA-перевірку пройдено!**\n\n"
                "Сайт відкривається, "
                "критичних JavaScript-помилок "
                "не знайдено.\n\n"
                "🔒 Тепер сайт можна вважати "
                "готовим до фінальної передачі."
            )

            order.site_url = live_url
            order.site_code = html_code

            return True

        # ----------------------------------------------------
        # FAILED
        # ----------------------------------------------------

        await thread.send(
            f"❌ **QA знайшов проблеми.**\n\n"
            f"🔧 Grox автоматично виправляє "
            f"сайт через Gemini.\n\n"
            f"Спроба: "
            f"**{attempt}/{MAX_WEBSITE_FIX_ATTEMPTS}**"
        )

        if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
            await thread.send(
                "❌ **Сайт не пройшов автоматичну "
                "перевірку після максимальної "
                "кількості спроб.**\n\n"
                "Фінальна передача заблокована."
            )

            return False

        # ----------------------------------------------------
        # GEMINI FIX
        # ----------------------------------------------------

        try:
            html_code = await fix_site_code(
                html_code,
                qa_result["report"],
                order.task
            )

        except Exception as error:
            print(
                "[WEBSITE FIX ERROR]"
            )

            print(
                f"{type(error).__name__}: {error}"
            )

            traceback.print_exc()

            await thread.send(
                "❌ Gemini не зміг виправити "
                "сайт автоматично.\n\n"
                "Фінальна передача заблокована."
            )

            return False

    return False


# ============================================================
# WAIT FOR PRICE APPROVAL
# ============================================================

async def wait_for_price_approval(
    thread: discord.Thread,
    order: Order
):
    """
    FIXED:
    The old version waited for one message and then silently
    returned False if the message was not an exact approval word.

    New behavior:
    - keeps listening until the client approves, changes the
      requirements, or the timeout expires;
    - accepts "Погоджуюсь", "погоджуюся", "так, погоджуюсь",
      "погоджуюсь!" etc.;
    - logs every received client message;
    - if the client changes the task, Grox recalculates the price
      instead of becoming silent.
    """

    await thread.send(
        "💬 Якщо вас влаштовує запропонована "
        "вартість, напишіть:\n\n"
        "**ПОГОДЖУЮСЬ**\n\n"
        "Якщо хочете змінити вимоги — "
        "напишіть, що саме потрібно змінити."
    )

    started_at = asyncio.get_running_loop().time()

    while True:
        elapsed = (
            asyncio.get_running_loop().time()
            - started_at
        )

        remaining_timeout = max(
            1,
            CLIENT_TIMEOUT - int(elapsed)
        )

        def check(msg: discord.Message):
            result = (
                msg.author.id == order.client_id
                and msg.channel.id == order.thread_id
                and not msg.author.bot
            )

            if result:
                print(
                    f"[PRICE APPROVAL MESSAGE] "
                    f"order=#{order.order_id} "
                    f"user={msg.author.id} "
                    f"content={msg.content!r}"
                )

            return result

        try:
            approval_message = await bot.wait_for(
                "message",
                check=check,
                timeout=remaining_timeout
            )

        except asyncio.TimeoutError:
            await thread.send(
                "⏰ Час очікування "
                "погодження ціни минув.\n\n"
                "Якщо ви хочете продовжити — "
                "напишіть у цьому thread."
            )

            order.status = "EXPIRED"
            return False

        order.approval_message_id = approval_message.id

        text = approval_message.content.strip()

        print(
            f"[PRICE APPROVAL CHECK] "
            f"order=#{order.order_id} "
            f"normalized={normalize_user_text(text)!r}"
        )

        # ----------------------------------------------------
        # APPROVED
        # ----------------------------------------------------

        if is_price_approval(text):
            order.client_approved_price = True

            await thread.send(
                "✅ **Ціну погоджено!**\n\n"
                f"💰 Узгоджена вартість: "
                f"**${order.budget}**"
            )

            print(
                f"[PRICE APPROVED] "
                f"order=#{order.order_id}"
            )

            return True

        # ----------------------------------------------------
        # NEW / CHANGED TASK
        # ----------------------------------------------------

        if len(text) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ Повідомлення занадто велике.\n"
                f"Максимум: {MAX_TASK_LENGTH} символів.\n\n"
                "Напишіть **ПОГОДЖУЮСЬ**, "
                "якщо залишаємо поточне ТЗ."
            )
            continue

        await thread.send(
            "🔄 **Бачу нові вимоги до проєкту.**\n\n"
            "🧠 Перераховую вартість за оновленим ТЗ..."
        )

        try:
            estimation = await estimate_project(
                text
            )

        except Exception as error:
            print(
                "[PRICE RE-ESTIMATION ERROR]"
            )

            print(
                f"{type(error).__name__}: {error}"
            )

            traceback.print_exc()

            await thread.send(
                "❌ Не вдалося перерахувати "
                "вартість за новими вимогами.\n\n"
                "Спробуйте описати зміни ще раз "
                "або напишіть **ПОГОДЖУЮСЬ** "
                "для поточної ціни."
            )

            continue

        order.task = text
        order.client_budget = extract_budget(
            text
        )

        order.project_type = (
            estimation["type"]
        )

        order.complexity = (
            estimation["complexity"]
        )

        order.price_reason = (
            estimation["reason"]
        )

        order.budget = (
            estimation["price"]
        )

        order.client_approved_price = False

        await thread.send(
            "📊 **Нову ціну розраховано.**\n\n"
            + price_status_message(order)
        )

        # Loop continues and waits again.
        started_at = asyncio.get_running_loop().time()


# ============================================================
# WAIT FOR PAYMENT CONFIRMATION
# ============================================================



# ============================================================
# PROCESS ONE ORDER
# ============================================================

async def process_order(
    message: discord.Message
):
    if message.id in active_orders:
        return

    active_orders.add(
        message.id
    )

    try:
        # ====================================================
        # CREATE ORDER
        # ====================================================

        order = create_order(
            message
        )

        # ====================================================
        # CREATE THREAD
        # ====================================================

        try:
            thread = await message.create_thread(
                name=(
                    f"Grox Order #"
                    f"{order.order_id}"
                ),
                auto_archive_duration=1440
            )

        except discord.Forbidden:
            await message.channel.send(
                f"{message.author.mention}, "
                f"я знайшов ваше замовлення, "
                f"але мені не вистачає прав "
                f"для створення thread."
            )

            order.status = "ERROR"
            return

        except Exception as error:
            print(
                f"[THREAD ERROR] "
                f"{type(error).__name__}: "
                f"{error!r}"
            )

            traceback.print_exc()

            await message.channel.send(
                f"{message.author.mention}, "
                f"виникла технічна помилка."
            )

            order.status = "ERROR"
            return

        order.thread_id = thread.id

        print(
            f"[THREAD CREATED] "
            f"order=#{order.order_id} "
            f"thread={thread.id}"
        )

        # ====================================================
        # GREETING
        # ====================================================

        await thread.send(
            f"👋 Вітаю, "
            f"{message.author.mention}!\n\n"
            f"🤖 **Grox прийняв ваше замовлення.**\n\n"
            f"🆔 Замовлення: "
            f"**#{order.order_id}**\n\n"
            f"💡 Вам не потрібно "
            f"заздалегідь визначати ціну.\n\n"
            f"📋 Надішліть детальне ТЗ.\n\n"
            f"🧠 Grox проаналізує "
            f"складність проєкту "
            f"та запропонує справедливу ціну."
        )

        # ====================================================
        # INITIAL CLIENT MESSAGE
        # ====================================================

        def check_initial_task(
            msg: discord.Message
        ):
            return (
                msg.author.id == order.client_id
                and msg.channel.id == thread.id
                and not msg.author.bot
            )

        try:
            initial_message = await bot.wait_for(
                "message",
                check=check_initial_task,
                timeout=CLIENT_TIMEOUT
            )
        except asyncio.TimeoutError:
            await thread.send(
                "⏰ Час очікування ТЗ минув.\n\n"
                "Якщо ви все ще хочете "
                "продовжити замовлення — "
                "створіть нове замовлення."
            )

            order.status = "EXPIRED"
            return

        first_task = initial_message.content.strip()

        if not first_task:
            await thread.send(
                "❌ ТЗ не може бути порожнім."
            )
            order.status = "ERROR"
            return

        if len(first_task) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ ТЗ занадто велике.\n"
                f"Максимум: {MAX_TASK_LENGTH} символів."
            )
            order.status = "ERROR"
            return

        # ====================================================
        # CONVERSATIONAL REQUIREMENT DISCOVERY
        # ====================================================

        final_task = await discover_requirements(
            order,
            thread,
            first_task
        )

        if not final_task:
            order.status = "ERROR"
            return

        if len(final_task) > MAX_TASK_LENGTH:
            final_task = final_task[:MAX_TASK_LENGTH]

        order.task = final_task

        await thread.send(
            "📋 **Фінальне ТЗ сформовано.**\n\n"
            "🧠 Тепер я оціню складність проєкту та "
            "запропоную ціну."
        )

        # ====================================================
        # CLIENT BUDGET
        # ====================================================

        order.client_budget = extract_budget(
            order.task
        )

        # ====================================================
        # ANALYZE
        # ====================================================

        await thread.send(
            "🧠 **Аналізую технічне завдання...**\n\n"
            "📊 Визначаю складність, "
            "обсяг роботи та справедливу ціну."
        )

        try:
            estimation = await estimate_project(
                order.task
            )

        except Exception as error:
            print(
                "========================================"
            )

            print(
                "[ORDER → PRICE ESTIMATION ERROR]"
            )

            print(
                f"Type: {type(error).__name__}"
            )

            print(
                f"Message: {error!r}"
            )

            traceback.print_exc()

            print(
                "========================================"
            )

            await thread.send(
                "❌ **Не вдалося оцінити "
                "вартість проєкту.**\n\n"
                "Спробуйте надіслати "
                "детальніше ТЗ."
            )

            order.status = "ERROR"
            return

        # ====================================================
        # SAVE ESTIMATION
        # ====================================================

        order.project_type = (
            estimation["type"]
        )

        order.complexity = (
            estimation["complexity"]
        )

        order.price_reason = (
            estimation["reason"]
        )

        order.budget = (
            estimation["price"]
        )

        order.status = (
            "WAITING_PRICE_APPROVAL"
        )

        # ====================================================
        # SHOW PRICE
        # ====================================================

        await thread.send(
            price_status_message(
                order
            )
        )

        # ====================================================
        # PRICE APPROVAL
        # ====================================================

        approved = await wait_for_price_approval(
            thread,
            order
        )

        if not approved:
            return

        # ====================================================
        # START WORK — PAYMENT TEMPORARILY DISABLED
        # ====================================================

        order.status = "IN_PROGRESS"

        await thread.send(
            f"✅ **Ціну погоджено!**\n\n"
            f"💰 Вартість проєкту: **${order.budget}**\n\n"
            "🤖 Grox починає виконання проєкту..."
        )

        # ====================================================
        # PROJECT TYPE DETECTION
        # ====================================================

        task_lower = order.task.casefold()

        website_project = any(
            keyword in task_lower
            for keyword in (
                "сайт",
                "website",
                "web",
                "лендинг",
                "landing",
                "вебсайт",
            )
        )

        # ====================================================
        # WEBSITE
        # ====================================================

        if website_project:
            try:
                success = await (
                    generate_test_and_fix_website(
                        order,
                        thread
                    )
                )

            except Exception as error:
                print(
                    "========================================"
                )

                print(
                    "[WEBSITE PROCESS ERROR]"
                )

                print(
                    f"Type: {type(error).__name__}"
                )

                print(
                    f"Message: {error!r}"
                )

                traceback.print_exc()

                print(
                    "========================================"
                )

                await thread.send(
                    "❌ **Не вдалося завершити "
                    "генерацію або перевірку сайту.**\n\n"
                    "🔒 Фінальна передача заблокована."
                )

                order.status = "ERROR"
                return

            if not success:
                order.status = "QA_FAILED"
                return

        # ====================================================
        # DISCORD BOT
        # ====================================================

        elif any(
            keyword in task_lower
            for keyword in (
                "discord bot",
                "discord бот",
                "бот",
                "bot",
            )
        ):
            await thread.send(
                "🤖 **Замовлення Discord-бота "
                "прийнято в роботу.**\n\n"
                "⚠️ У цій версії Grox ще не "
                "запускає сторонній згенерований "
                "бот у своєму середовищі.\n\n"
                "🔒 Фінальна передача буде "
                "дозволена тільки після "
                "завершення доступної перевірки."
            )

        # ====================================================
        # OTHER PROJECT
        # ====================================================

        else:
            await thread.send(
                "🛠️ **Виконую проєкт "
                "відповідно до ТЗ...**"
            )

        # ====================================================
        # PROJECT READY — PAYMENT TEMPORARILY DISABLED
        # ====================================================

        order.status = "COMPLETED"

        # ====================================================
        # SITE PREVIEW
        # ====================================================

        if order.site_url:
            await thread.send(
                f"🌐 **Готовий результат:**\n"
                f"{order.site_url}"
            )

        # ====================================================
        # COMPLETED
        # ====================================================

        print(
            f"[SUCCESS] "
            f"Order #{order.order_id} "
            f"completed."
        )

        order.status = "COMPLETED"

        await thread.send(
            completed_message(
                order
            )
        )

        if order.site_url:
            await thread.send(
                f"🔗 **Фінальний сайт:**\n"
                f"{order.site_url}"
            )

        # ====================================================
        # POST-DELIVERY CLIENT CONVERSATION
        # ====================================================

        await post_delivery_conversation(
            order,
            thread
        )

    except Exception as error:
        print(
            "========================================"
        )

        print(
            "🔥 ORDER ERROR"
        )

        print(
            f"Type: {type(error).__name__}"
        )

        print(
            f"Message: {error!r}"
        )

        traceback.print_exc()

        print(
            "========================================"
        )

        try:
            await message.channel.send(
                f"{message.author.mention}, "
                f"під час обробки замовлення "
                f"сталася технічна помилка."
            )

        except Exception:
            pass

    finally:
        active_orders.discard(
            message.id
        )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():
    print(
        "========================================"
    )

    print(
        f"🤖 Grox ONLINE: {bot.user}"
    )

    print(
        f"🆔 Bot ID: {bot.user.id}"
    )

    print(
        f"🧠 Gemini model: {GEMINI_MODEL}"
    )

    print(
        f"📡 Client channel: "
        f"{CLIENT_CHANNEL_ID}"
    )

    print(
        f"💰 Minimum project price: "
        f"${MINIMUM_PROJECT_PRICE}"
    )

    print(
        f"💰 Maximum project price: "
        f"${MAXIMUM_BUDGET}"
    )

    print(
        f"🧪 Website QA enabled: "
        f"YES"
    )

    print(
        f"🔧 Max website fix attempts: "
        f"{MAX_WEBSITE_FIX_ATTEMPTS}"
    )

    print(
        f"💬 Robust price approval: "
        f"YES"
    )

    print(
        "🚀 Grox готовий!"
    )

    print(
        "========================================"
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):
    if message.author.bot:
        return

    # Always process commands first.
    await bot.process_commands(
        message
    )

    # Only detect NEW orders in the configured client channel.
    #
    # Messages inside Grox threads are intentionally NOT treated
    # as new orders. They are handled by the active wait_for()
    # listeners in process_order()/wait_for_price_approval().
    if message.channel.id != CLIENT_CHANNEL_ID:
        return

    if not is_order_message(
        message.content
    ):
        return

    if message.id in active_orders:
        return

    print(
        f"[ORDER DETECTED] "
        f"{message.author} | "
        f"Message: "
        f"{message.content[:200]}"
    )

    asyncio.create_task(
        process_order(
            message
        )
    )


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

async def health_handler(
    request
):
    return web.Response(
        text="Grox is running! 🤖"
    )


async def start_health_server():
    app = web.Application()

    app.router.add_get(
        "/",
        health_handler
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        f"[HEALTH] Server listening "
        f"on port {PORT}"
    )

    return runner


# ============================================================
# MAIN
# ============================================================

async def main():
    print(
        "🚀 Starting Grox..."
    )

    health_runner = (
        await start_health_server()
    )

    try:
        await bot.start(
            DISCORD_TOKEN
        )

    finally:
        await bot.close()
        await health_runner.cleanup()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print(
            "🛑 Grox stopped."
        )
