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

# ============================================================
# PAYMENT WALLET
# ============================================================

CRYPTO_WALLET = os.getenv("CRYPTO_WALLET")

CLIENT_CHANNEL_ID = os.getenv("CLIENT_CHANNEL_ID")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.7-flash"
)

CLIENT_TIMEOUT = int(
    os.getenv("CLIENT_TIMEOUT", "600")
)

PORT = int(
    os.getenv("PORT", "10000")
)


# ============================================================
# PAYMENT / SECURITY SETTINGS
# ============================================================

TEST_PAYMENT_MODE = os.getenv(
    "TEST_PAYMENT_MODE",
    "true"
).lower() == "true"

ADMIN_USER_ID = os.getenv(
    "ADMIN_USER_ID"
)

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

# Максимальна кількість автоматичних виправлень сайту.
MAX_WEBSITE_FIX_ATTEMPTS = 3


# ============================================================
# CHECK ENVIRONMENT
# ============================================================

required_variables = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "VERCEL_TOKEN": VERCEL_TOKEN,
    "CLIENT_CHANNEL_ID": CLIENT_CHANNEL_ID,
    "CRYPTO_WALLET": CRYPTO_WALLET,
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

    CLIENT_CHANNEL_ID = int(
        CLIENT_CHANNEL_ID
    )

except ValueError:

    raise RuntimeError(
        "CLIENT_CHANNEL_ID повинен бути числом."
    )


if ADMIN_USER_ID:

    try:

        ADMIN_USER_ID = int(
            ADMIN_USER_ID
        )

    except ValueError:

        raise RuntimeError(
            "ADMIN_USER_ID повинен бути числом."
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

    print(
        "========== GEMINI CLIENT ERROR =========="
    )

    print(
        f"Type: {type(error).__name__}"
    )

    print(
        f"Message: {error!r}"
    )

    traceback.print_exc()

    print(
        "========================================="
    )

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

    deposit: int = 0

    remaining: int = 0

    thread_id: int = 0

    task: str = ""

    project_type: str = ""

    complexity: str = ""

    price_reason: str = ""

    client_budget: int | None = None

    client_approved_price: bool = False

    payment1_confirmed: bool = False

    payment2_confirmed: bool = False

    site_url: str | None = None

    site_code: str | None = None

    status: str = "WAITING_TZ"


orders = {}

active_orders = set()

next_order_id = 1000


# ============================================================
# ORDER HELPERS
# ============================================================

def create_order(
    message: discord.Message
):

    global next_order_id

    next_order_id += 1

    order = Order(

        order_id=next_order_id,

        discord_message_id=message.id,

        client_id=message.author.id,

        client_name=message.author.name,
    )

    orders[
        order.order_id
    ] = order

    return order


# ============================================================
# ORDER STATUS MESSAGES
# ============================================================

def price_status_message(
    order: Order
):

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

        f"💵 Передоплата: "
        f"**${order.deposit}**\n"

        f"💵 Після завершення: "
        f"**${order.remaining}**\n\n"

        f"📝 **Чому така ціна:**\n"
        f"{order.price_reason}\n\n"

        f"⏳ Очікую погодження ціни."
    )


def deposit_status_message(
    order: Order
):

    return (

        f"🟡 **Замовлення #{order.order_id}**\n\n"

        f"💰 Загальна сума: "
        f"**${order.budget}**\n"

        f"💵 Передоплата: "
        f"**${order.deposit}**\n\n"

        f"🏦 **Адреса для оплати:**\n"
        f"`{CRYPTO_WALLET}`\n\n"

        f"⏳ **Статус: Очікується передоплата**\n\n"

        f"🔒 Робота ще не розпочата."
    )


def deposit_confirmed_message(
    order: Order
):

    return (

        f"🟢 **Замовлення #{order.order_id}**\n\n"

        f"💰 Загальна сума: "
        f"**${order.budget}**\n"

        f"✅ Передоплата "
        f"**${order.deposit}** підтверджена.\n\n"

        f"🛠️ **Можна починати роботу!**"
    )


def final_payment_message(
    order: Order
):

    return (

        f"🔵 **Замовлення #{order.order_id}**\n\n"

        f"🎉 **Проєкт готовий!**\n\n"

        f"💰 Залишок до оплати: "
        f"**${order.remaining}**\n\n"

        f"🏦 **Адреса для фінальної оплати:**\n"
        f"`{CRYPTO_WALLET}`\n\n"

        f"💳 **До оплати: "
        f"${order.remaining}**\n\n"

        f"🔒 **Фінальна передача заблокована.**\n\n"

        f"📋 Після оплати надішліть підтвердження.\n\n"

        f"⏳ Очікується друга оплата."
    )


def completed_message(
    order: Order
):

    return (

        f"🟢 **Замовлення #{order.order_id} завершене!**\n\n"

        f"💰 Загальна сума: "
        f"**${order.budget}**\n"

        f"✅ Передоплата підтверджена\n"

        f"✅ Фінальна оплата підтверджена\n"

        f"🔓 Фінальна передача дозволена."
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


def contains_service_keyword(
    text: str
) -> bool:

    text = text.lower()

    return any(

        keyword in text

        for keyword in KEYWORDS
    )


def is_order_message(
    text: str
) -> bool:

    return contains_service_keyword(
        text
    )


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


def extract_budget(
    text: str
):

    match = BUDGET_PATTERN.search(
        text
    )

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

                return int(
                    cleaned
                )

            except ValueError:

                return None


    return None


# ============================================================
# PAYMENT TEST SYSTEM
# ============================================================

async def wait_for_test_payment(
    thread: discord.Thread,
    order: Order,
    payment_number: int
):

    if not TEST_PAYMENT_MODE:

        return False


    if ADMIN_USER_ID is None:

        await thread.send(

            "⚠️ Тестова платіжна система "
            "не налаштована: "
            "`ADMIN_USER_ID` відсутній."
        )

        return False


    amount = (

        order.deposit
        if payment_number == 1
        else order.remaining
    )


    payment_name = (

        "передоплати"
        if payment_number == 1
        else "фінальної оплати"
    )


    await thread.send(

        f"💳 **Оплата №{payment_number} — "
        f"{payment_name}**\n\n"

        f"💰 **Сума до оплати: ${amount}**\n\n"

        f"🏦 **Адреса для оплати:**\n"
        f"`{CRYPTO_WALLET}`\n\n"

        f"📋 Після здійснення оплати "
        f"надішліть підтвердження.\n\n"

        f"⏳ Це тестовий режим.\n\n"

        f"Для тесту адміністратор може "
        f"підтвердити оплату командою:\n\n"

        f"`!confirm "
        f"{order.order_id} "
        f"{payment_number}`"
    )


    return True


# ============================================================
# ADMIN PAYMENT CONFIRMATION
# ============================================================

@bot.command(
    name="confirm"
)
@commands.guild_only()
async def confirm_payment(
    ctx,
    order_id: int,
    payment_number: int
):

    if ADMIN_USER_ID is None:

        await ctx.send(
            "❌ ADMIN_USER_ID не налаштований."
        )

        return


    if ctx.author.id != ADMIN_USER_ID:

        await ctx.send(

            "❌ У вас немає прав "
            "для підтвердження платежу."
        )

        return


    if not TEST_PAYMENT_MODE:

        await ctx.send(

            "❌ Тестове підтвердження "
            "вимкнене."
        )

        return


    if payment_number not in (1, 2):

        await ctx.send(

            "❌ Номер платежу має бути "
            "1 або 2."
        )

        return


    order = orders.get(
        order_id
    )


    if order is None:

        await ctx.send(
            "❌ Замовлення не знайдено."
        )

        return


    # ========================================================
    # FIRST PAYMENT
    # ========================================================

    if payment_number == 1:

        if not order.client_approved_price:

            await ctx.send(

                "❌ Клієнт ще не погодив "
                "ціну цього замовлення."
            )

            return


        if order.payment1_confirmed:

            await ctx.send(

                "ℹ️ Перша оплата "
                "вже підтверджена."
            )

            return


        order.payment1_confirmed = True

        order.status = "IN_PROGRESS"


        thread = bot.get_channel(
            order.thread_id
        )


        if thread:

            await thread.send(
                deposit_confirmed_message(
                    order
                )
            )


        await ctx.send(

            f"✅ Передоплату "
            f"${order.deposit} "
            f"для #{order.order_id} "
            f"підтверджено."
        )

        return


    # ========================================================
    # SECOND PAYMENT
    # ========================================================

    if payment_number == 2:

        if not order.payment1_confirmed:

            await ctx.send(

                "❌ Не можна підтвердити "
                "другу оплату до першої."
            )

            return


        if order.status != "WAITING_FINAL_PAYMENT":

            await ctx.send(

                "❌ Проєкт ще не пройшов "
                "усі необхідні перевірки."
            )

            return


        if order.payment2_confirmed:

            await ctx.send(

                "ℹ️ Друга оплата "
                "вже підтверджена."
            )

            return


        order.payment2_confirmed = True

        order.status = "COMPLETED"


        thread = bot.get_channel(
            order.thread_id
        )


        if thread:

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


        await ctx.send(

            f"✅ Фінальну оплату "
            f"${order.remaining} "
            f"для #{order.order_id} "
            f"підтверджено."
        )


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

                    max_output_tokens=
                    max_output_tokens
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


            error_text = str(
                error
            ).lower()


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
            ) + random.uniform(
                0,
                1
            )


            print(

                f"[GEMINI] Повтор через "
                f"{delay:.1f}s..."
            )


            await asyncio.sleep(
                delay
            )


    if last_error:

        raise last_error


    raise RuntimeError(
        "Gemini не зміг виконати запит."
    )


# ============================================================
# PROJECT PRICE ESTIMATION
# ============================================================

async def estimate_project(
    client_task: str
):

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

async def generate_site_code(
    client_task: str
) -> str:

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

async def test_website(
    url: str
):

    print(
        f"[QA] Starting website test: {url}"
    )


    errors = []

    warnings = []

    console_errors = []

    failed_requests = []

    clicked_elements = 0


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

    await thread.send(

        "💬 Якщо вас влаштовує запропонована "
        "вартість, напишіть:\n\n"

        "**ПОГОДЖУЮСЬ**\n\n"

        "Якщо хочете змінити вимоги — "
        "напишіть нове ТЗ."
    )


    def check(
        msg: discord.Message
    ):

        return (

            msg.author.id
            == order.client_id

            and

            msg.channel.id
            == order.thread_id

            and

            not msg.author.bot
        )


    try:

        approval_message = await bot.wait_for(

            "message",

            check=check,

            timeout=CLIENT_TIMEOUT
        )


    except asyncio.TimeoutError:

        await thread.send(

            "⏰ Час очікування "
            "погодження ціни минув."
        )

        return False


    text = (
        approval_message.content
        .strip()
        .lower()
    )


    approval_words = [

        "погоджуюсь",
        "погоджуюся",
        "згоден",
        "згодна",
        "agree",
        "approved",
        "yes",

    ]


    if text in approval_words:

        order.client_approved_price = True

        return True


    await thread.send(

        "ℹ️ Ціну не було підтверджено.\n\n"

        "Щоб погодити ціну, напишіть:\n"

        "**ПОГОДЖУЮСЬ**"
    )


    return False


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

            return


        order.thread_id = thread.id


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
        # WAIT FOR TЗ
        # ====================================================

        def check(
            msg: discord.Message
        ):

            return (

                msg.author.id
                == order.client_id

                and

                msg.channel.id
                == thread.id

                and

                not msg.author.bot
            )


        try:

            client_message = await bot.wait_for(

                "message",

                check=check,

                timeout=CLIENT_TIMEOUT
            )


        except asyncio.TimeoutError:

            await thread.send(

                "⏰ Час очікування ТЗ минув.\n\n"

                "Якщо ви все ще хочете "
                "продовжити замовлення — "
                "створіть нове замовлення."
            )

            return


        task = client_message.content.strip()


        # ====================================================
        # TASK VALIDATION
        # ====================================================

        if not task:

            await thread.send(
                "❌ ТЗ не може бути порожнім."
            )

            return


        if len(task) > MAX_TASK_LENGTH:

            await thread.send(

                "❌ ТЗ занадто велике.\n"

                f"Максимум: "
                f"{MAX_TASK_LENGTH} символів."
            )

            return


        order.task = task


        # ====================================================
        # CLIENT BUDGET
        # ====================================================

        order.client_budget = extract_budget(
            task
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

        order.deposit = (
            order.budget + 1
        ) // 2

        order.remaining = (
            order.budget
            - order.deposit
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


        order.status = (
            "WAITING_DEPOSIT"
        )


        # ====================================================
        # PRICE APPROVED + PAYMENT ADDRESS
        # ====================================================

        await thread.send(

            f"✅ **Ціну погоджено!**\n\n"

            f"💰 Загальна сума: "
            f"**${order.budget}**\n"

            f"💵 Передоплата: "
            f"**${order.deposit}**\n"

            f"💵 Після завершення: "
            f"**${order.remaining}**\n\n"

            f"💳 **Для початку роботи "
            f"необхідна передоплата.**\n\n"

            f"🏦 **Адреса для оплати:**\n"
            f"`{CRYPTO_WALLET}`\n\n"

            f"💰 **До оплати зараз: "
            f"${order.deposit}**\n\n"

            f"📋 Після оплати надішліть "
            f"підтвердження."
        )


        # ====================================================
        # FIRST PAYMENT
        # ====================================================

        await wait_for_test_payment(

            thread,

            order,

            1
        )


        await thread.send(

            "⏳ **Очікую підтвердження "
            "передоплати.**"
        )


        while not order.payment1_confirmed:

            await asyncio.sleep(
                2
            )


        # ====================================================
        # START WORK
        # ====================================================

        await thread.send(

            "🟢 **Передоплату підтверджено!**\n\n"

            "🤖 Grox починає виконання "
            "проєкту..."
        )


        # ====================================================
        # PROJECT TYPE DETECTION
        # ====================================================

        task_lower = task.lower()


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

                return


            if not success:

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
        # PROJECT READY
        # ====================================================

        order.status = (
            "WAITING_FINAL_PAYMENT"
        )


        await thread.send(

            final_payment_message(
                order
            )
        )


        # ====================================================
        # SITE PREVIEW
        # ====================================================

        if order.site_url:

            await thread.send(

                f"🌐 **Перевірений результат:**\n"
                f"{order.site_url}"
            )


        # ====================================================
        # SECOND PAYMENT
        # ====================================================

        await wait_for_test_payment(

            thread,

            order,

            2
        )


        await thread.send(

            "⏳ **Очікую підтвердження "
            "другої оплати.**"
        )


        while not order.payment2_confirmed:

            await asyncio.sleep(
                2
            )


        # ====================================================
        # COMPLETED
        # ====================================================

        print(

            f"[SUCCESS] "
            f"Order #{order.order_id} "
            f"completed."
        )


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
        f"💳 Test payment mode: "
        f"{TEST_PAYMENT_MODE}"
    )

    print(
        f"🏦 Payment wallet: "
        f"{CRYPTO_WALLET}"
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


    await bot.process_commands(
        message
    )


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
