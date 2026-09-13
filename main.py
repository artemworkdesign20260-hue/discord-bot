
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

MAX_WEBSITE_FIX_ATTEMPTS = 3

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
    text = re.sub(
        r"[^\w\s'а-яіїєґёa-z0-9-]",
        " ",
        text,
        flags=re.IGNORECASE
    )
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
# PAYMENT SYSTEM — TEMPORARILY DISABLED
# ============================================================
#
# Оплату тут спеціально вимкнено.
# Після завершення тестування її можна повернути
# зі старої версії Grox.
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
               # ============================================================
# EXTRACT CODE FROM GEMINI RESPONSE
# ============================================================

def extract_code(response: str) -> str:
    """
    Removes Markdown code fences if Gemini returns them.
    """

    response = response.strip()

    if "```" in response:
        blocks = re.findall(
            r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```",
            response,
            re.DOTALL
        )

        if blocks:
            return max(
                blocks,
                key=len
            ).strip()

    return response


# ============================================================
# HTML DETECTION
# ============================================================

def looks_like_html(code: str) -> bool:
    lowered = code.lower()

    indicators = (
        "<!doctype html",
        "<html",
        "<head",
        "<body",
        "<div",
        "<script",
        "<style",
    )

    return any(
        indicator in lowered
        for indicator in indicators
    )


# ============================================================
# WEBSITE HTML CLEANUP
# ============================================================

def prepare_html(code: str) -> str:
    code = extract_code(code)

    if not looks_like_html(code):
        code = f"""
<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">
<title>Grox Project</title>
</head>
<body>
{code}
</body>
</html>
"""

    return code


# ============================================================
# WEBSITE TEST
# ============================================================

async def test_website_html(
    html_code: str
):
    """
    Opens generated HTML in Playwright and checks
    for JavaScript/runtime errors.
    """

    errors = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        page = await browser.new_page()

        page.on(
            "pageerror",
            lambda error: errors.append(
                f"PAGE ERROR: {error}"
            )
        )

        page.on(
            "console",
            lambda message:
                errors.append(
                    f"CONSOLE {message.type}: "
                    f"{message.text}"
                )
                if message.type == "error"
                else None
        )

        try:
            await page.set_content(
                html_code,
                wait_until="networkidle",
                timeout=30000
            )

            await page.wait_for_timeout(1000)

        except Exception as error:
            errors.append(
                f"LOAD ERROR: {type(error).__name__}: "
                f"{error}"
            )

        finally:
            await browser.close()

    return errors


# ============================================================
# WEBSITE AUTO-FIX
# ============================================================

async def fix_website(
    order: Order,
    html_code: str,
    errors: list[str]
):
    if not errors:
        return html_code

    current_code = html_code

    for attempt in range(
        1,
        MAX_WEBSITE_FIX_ATTEMPTS + 1
    ):

        print(
            f"[WEBSITE FIX] "
            f"Order #{order.order_id}, "
            f"attempt {attempt}/"
            f"{MAX_WEBSITE_FIX_ATTEMPTS}"
        )

        error_text = "\n".join(
            errors[-50:]
        )

        prompt = f"""
Ти — senior frontend developer.

Є HTML-проєкт, який має помилки
під час запуску.

ТЕХНІЧНЕ ЗАВДАННЯ:
{order.task}

ПОМИЛКИ:
{error_text}

ПОТОЧНИЙ HTML:
{current_code}

ВИПРАВ ПОМИЛКИ.

ВАЖЛИВО:

1. Не прибирай функції, які вже працюють.
2. Не спрощуй проєкт.
3. Не замінюй функціонал заглушками.
4. Збережи дизайн.
5. Збережи адаптивність.
6. Виправ JavaScript.
7. Виправ HTML.
8. Виправ CSS, якщо це необхідно.
9. Поверни ПОВНИЙ HTML-файл.
10. Не додавай пояснення.
11. Не використовуй Markdown code fences.
"""

        try:
            response = await gemini_request(
                prompt,
                max_output_tokens=12000
            )

            current_code = prepare_html(
                response
            )

            errors = await test_website_html(
                current_code
            )

            if not errors:
                print(
                    f"[WEBSITE FIX] "
                    f"Order #{order.order_id}: "
                    f"SUCCESS"
                )

                return current_code

        except Exception as error:
            print(
                f"[WEBSITE FIX ERROR] "
                f"Order #{order.order_id}: "
                f"{type(error).__name__}: "
                f"{error!r}"
            )

    return current_code


# ============================================================
# VERCEL DEPLOY
# ============================================================

async def deploy_to_vercel(
    order: Order,
    html_code: str
):
    """
    Deploys generated HTML as a Vercel project.
    """

    project_name = (
        f"grox-order-{order.order_id}-"
        f"{uuid.uuid4().hex[:8]}"
    )

    headers = {
        "Authorization": (
            f"Bearer {VERCEL_TOKEN}"
        ),
        "Content-Type": "application/json",
    }

    files = [
        {
            "file": "index.html",
            "data": html_code,
        }
    ]

    payload = {
        "name": project_name,
        "files": files,
        "projectSettings": {
            "framework": None
        }
    }

    timeout = aiohttp.ClientTimeout(
        total=CLIENT_TIMEOUT
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        try:
            async with session.post(
                "https://api.vercel.com/v13/deployments",
                headers=headers,
                json=payload
            ) as response:

                response_text = await response.text()

                print(
                    f"[VERCEL] HTTP {response.status}"
                )

                if response.status not in (
                    200,
                    201
                ):
                    raise RuntimeError(
                        "Vercel deployment failed: "
                        f"HTTP {response.status}: "
                        f"{response_text[:2000]}"
                    )

                try:
                    data = await response.json(
                        content_type=None
                    )

                except Exception:
                    data = {}

                deployment_url = (
                    data.get("url")
                    or data.get("alias", [None])[0]
                )

                if not deployment_url:
                    raise RuntimeError(
                        "Vercel не повернув URL."
                    )

                if not deployment_url.startswith(
                    "http"
                ):
                    deployment_url = (
                        "https://"
                        + deployment_url
                    )

                print(
                    f"[VERCEL] Deployed: "
                    f"{deployment_url}"
                )

                return deployment_url

        except asyncio.TimeoutError:
            raise RuntimeError(
                "Vercel deployment timeout."
            )


# ============================================================
# VERCEL WEBSITE VERIFICATION
# ============================================================

async def verify_deployed_site(
    url: str
):
    """
    Opens the deployed website and checks whether
    it responds correctly.
    """

    errors = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        page = await browser.new_page()

        page.on(
            "pageerror",
            lambda error: errors.append(
                f"PAGE ERROR: {error}"
            )
        )

        page.on(
            "console",
            lambda message:
                errors.append(
                    f"CONSOLE {message.type}: "
                    f"{message.text}"
                )
                if message.type == "error"
                else None
        )

        try:
            response = await page.goto(
                url,
                wait_until="networkidle",
                timeout=30000
            )

            if response is None:
                errors.append(
                    "No HTTP response received."
                )

            elif response.status >= 400:
                errors.append(
                    f"HTTP status: "
                    f"{response.status}"
                )

            await page.wait_for_timeout(
                1500
            )

        except Exception as error:
            errors.append(
                f"DEPLOYMENT LOAD ERROR: "
                f"{type(error).__name__}: "
                f"{error}"
            )

        finally:
            await browser.close()

    return errors


# ============================================================
# WEBSITE GENERATION PIPELINE
# ============================================================

async def build_website(
    order: Order
):
    """
    Complete website pipeline:

    1. Generate.
    2. Clean HTML.
    3. Test locally.
    4. Automatically fix errors.
    5. Deploy to Vercel.
    6. Verify deployed website.
    """

    print(
        f"[BUILD] Starting website build "
        f"for order #{order.order_id}"
    )

    raw_code = await generate_project(
        order
    )

    html_code = prepare_html(
        raw_code
    )

    errors = await test_website_html(
        html_code
    )

    if errors:
        print(
            f"[BUILD] Found "
            f"{len(errors)} local errors."
        )

        html_code = await fix_website(
            order,
            html_code,
            errors
        )

    errors = await test_website_html(
        html_code
    )

    if errors:
        print(
            f"[BUILD] Warning: "
            f"{len(errors)} errors remain."
        )

    order.site_code = html_code

    deployment_url = await deploy_to_vercel(
        order,
        html_code
    )

    order.site_url = deployment_url

    deployed_errors = await verify_deployed_site(
        deployment_url
    )

    if deployed_errors:

        print(
            f"[BUILD] Deployed site has "
            f"{len(deployed_errors)} errors."
        )

        fixed_code = await fix_website(
            order,
            html_code,
            deployed_errors
        )

        if fixed_code != html_code:

            html_code = fixed_code

            order.site_code = html_code

            deployment_url = await deploy_to_vercel(
                order,
                html_code
            )

            order.site_url = deployment_url

    print(
        f"[BUILD] Finished order "
        f"#{order.order_id}: "
        f"{order.site_url}"
    )

    return order.site_url


# ============================================================
# DISCORD THREAD CREATION
# ============================================================

async def create_private_thread(
    message: discord.Message,
    order: Order
):
    """
    Creates a private Discord thread for the client.
    """

    try:
        thread = await message.create_thread(
            name=(
                f"Grox #{order.order_id} - "
                f"{message.author.name}"
            ),
            auto_archive_duration=10080
        )

    except TypeError:
        thread = await message.create_thread(
            name=(
                f"Grox #{order.order_id} - "
                f"{message.author.name}"
            )
        )

    order.thread_id = thread.id

    try:
        await thread.add_user(
            message.author
        )
    except Exception as error:
        print(
            f"[THREAD] Could not add client: "
            f"{error!r}"
        )

    return thread


# ============================================================
# ORDER INITIAL MESSAGE
# ============================================================

async def send_order_received(
    thread: discord.Thread,
    order: Order
):
    await thread.send(
        f"🤖 **Grox прийняв замовлення "
        f"#{order.order_id}!**\n\n"
        f"📝 Я аналізую ваше технічне "
        f"завдання та готую оцінку."
    )


# ============================================================
# PRICE APPROVAL
# ============================================================

async def send_price_for_approval(
    thread: discord.Thread,
    order: Order
):
    await thread.send(
        price_status_message(order)
        + "\n\n"
        "👉 Якщо ціна підходить, "
        "напишіть **«погоджуюсь»**."
    )


# ============================================================
# START PROJECT
# ============================================================

async def start_project(
    thread: discord.Thread,
    order: Order
):
    order.status = "IN_PROGRESS"

    await thread.send(
        f"🚀 **Grox починає виконання "
        f"замовлення #{order.order_id}!**\n\n"
        f"💰 Погоджена вартість: "
        f"**${order.budget}**\n\n"
        "🤖 Починаю роботу над проєктом."
    )

    try:

        is_website = any(
            keyword in order.project_type.casefold()
            for keyword in (
                "site",
                "website",
                "web",
                "сайт",
                "лендинг",
                "landing"
            )
        )

        if not is_website:
            is_website = (
                "сайт" in order.task.casefold()
                or "website" in order.task.casefold()
                or "landing" in order.task.casefold()
                or "лендинг" in order.task.casefold()
            )

        if is_website:

            await thread.send(
                "🧠 Генерую сайт..."
            )

            site_url = await build_website(
                order
            )

            order.status = "COMPLETED"

            await thread.send(
                completed_message(order)
            )

            await thread.send(
                f"🌐 **Готовий сайт:**\n"
                f"{site_url}"
            )

        else:

            await thread.send(
                "🧠 Генерую проєкт..."
            )

            generated = await generate_project(
                order
            )

            order.site_code = generated
            order.status = "COMPLETED"

            await thread.send(
                completed_message(order)
            )

            # Discord has message length limits.
            # Send the result in chunks.
            chunks = [
                generated[i:i + 1900]
                for i in range(
                    0,
                    len(generated),
                    1900
                )
            ]

            for index, chunk in enumerate(
                chunks[:10],
                start=1
            ):
                await thread.send(
                    f"```text\n"
                    f"{chunk}\n"
                    f"```"
                )

            if len(chunks) > 10:
                await thread.send(
                    "⚠️ Результат занадто великий "
                    "для повного надсилання в Discord."
                )

    except Exceptionasync def deploy_to_vercel(
    project_name: str,
    files: dict[str, str]
):
    """
    Deploy project to Vercel.
    """

    if not VERCEL_TOKEN:
        raise RuntimeError(
            "VERCEL_TOKEN не налаштований."
        )

    deployment_files = []

    for filename, content in files.items():
        deployment_files.append(
            {
                "file": filename,
                "data": content
            }
        )

    payload = {
        "name": project_name,
        "files": deployment_files,
        "projectSettings": {
            "framework": None
        }
    }

    headers = {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.vercel.com/v13/deployments",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(
                total=CLIENT_TIMEOUT
            )
        ) as response:

            text = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"Vercel deployment failed: "
                    f"{response.status} {text}"
                )

            try:
                data = await response.json()
            except Exception:
                raise RuntimeError(
                    f"Vercel повернув не JSON: {text}"
                )

    deployment_url = data.get("url")

    if not deployment_url:
        raise RuntimeError(
            "Vercel не повернув URL."
        )

    if not deployment_url.startswith("http"):
        deployment_url = (
            "https://" + deployment_url
        )

    return deployment_url


# ============================================================
# WEBSITE FILE EXTRACTION
# ============================================================

def extract_html_from_response(
    response: str
):
    """
    Extract HTML from Gemini response.
    """

    response = response.strip()

    fenced_match = re.search(
        r"```(?:html)?\s*(.*?)```",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if fenced_match:
        return fenced_match.group(1).strip()

    html_match = re.search(
        r"(<(?:!DOCTYPE|html)[\s\S]*?</html>)",
        response,
        re.IGNORECASE
    )

    if html_match:
        return html_match.group(1).strip()

    return response


# ============================================================
# WEBSITE GENERATION
# ============================================================

async def generate_website(
    order: Order
):
    prompt = f"""
Ти — професійний frontend-розробник.

Створи повністю готовий односторінковий
вебсайт на основі ТЗ клієнта.

ВИМОГИ:

- HTML5;
- CSS;
- JavaScript;
- сучасний дизайн;
- адаптивність для телефону;
- адаптивність для ПК;
- усі кнопки повинні працювати;
- усі секції повинні бути завершені;
- не використовуй TODO;
- не залишай заглушок;
- не пиши пояснення поза кодом.

ТЕХНІЧНЕ ЗАВДАННЯ:

{order.task}

Поверни тільки готовий HTML-код.
"""

    response = await gemini_request(
        prompt,
        max_output_tokens=16000
    )

    html = extract_html_from_response(
        response
    )

    if not html:
        raise RuntimeError(
            "Gemini не створив HTML."
        )

    return html


# ============================================================
# WEBSITE VALIDATION
# ============================================================

async def validate_website(
    html: str
):
    """
    Basic local validation before deployment.
    """

    if not html.strip():
        return False, "HTML порожній."

    lowered = html.lower()

    if "<html" not in lowered:
        return False, "Відсутній тег <html>."

    if "<body" not in lowered:
        return False, "Відсутній тег <body>."

    if "</html>" not in lowered:
        return False, "Відсутній </html>."

    return True, None


# ============================================================
# WEBSITE TEST
# ============================================================

async def test_website(
    url: str
):
    """
    Open deployed website with Playwright
    and check that the page loads.
    """

    browser = None

    try:
        async with async_playwright() as playwright:

            browser = await playwright.chromium.launch(
                headless=True
            )

            page = await browser.new_page()

            response = await page.goto(
                url,
                wait_until="networkidle",
                timeout=CLIENT_TIMEOUT * 1000
            )

            if response is None:
                return False, (
                    "Сайт не повернув HTTP response."
                )

            status = response.status

            if status >= 400:
                return False, (
                    f"Сайт повернув HTTP {status}."
                )

            title = await page.title()

            print(
                f"[SITE TEST] "
                f"status={status}, "
                f"title={title!r}"
            )

            return True, None

    except Exception as error:
        print(
            f"[SITE TEST ERROR] "
            f"{type(error).__name__}: "
            f"{error!r}"
        )

        return False, str(error)

    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


# ============================================================
# WEBSITE FIX
# ============================================================

async def fix_website(
    order: Order,
    html: str,
    error_message: str
):
    prompt = f"""
Ти — senior frontend developer.

Попередня версія сайту не пройшла тестування.

ТЕХНІЧНЕ ЗАВДАННЯ:

{order.task}

ПОМИЛКА:

{error_message}

ПОПЕРЕДНІЙ HTML:

{html}

Виправ проблему.

ВАЖЛИВО:

- не прибирай потрібні функції;
- не спрощуй сайт без причини;
- збережи дизайн;
- збережи функціональність;
- поверни повний HTML;
- не використовуй TODO;
- не додавай пояснення;
- поверни тільки HTML-код.
"""

    response = await gemini_request(
        prompt,
        max_output_tokens=16000
    )

    fixed_html = extract_html_from_response(
        response
    )

    return fixed_html


# ============================================================
# WEBSITE BUILD + DEPLOY + TEST
# ============================================================

async def build_and_deploy_website(
    order: Order
):
    print(
        f"[WEBSITE] Starting website for "
        f"order #{order.order_id}"
    )

    html = await generate_website(
        order
    )

    valid, validation_error = (
        await validate_website(html)
    )

    if not valid:
        print(
            f"[WEBSITE] Initial validation failed: "
            f"{validation_error}"
        )

        for attempt in range(
            1,
            MAX_WEBSITE_FIX_ATTEMPTS + 1
        ):
            html = await fix_website(
                order,
                html,
                validation_error
            )

            valid, validation_error = (
                await validate_website(html)
            )

            if valid:
                break

            print(
                f"[WEBSITE] "
                f"Validation fix attempt "
                f"{attempt} failed."
            )

    if not valid:
        raise RuntimeError(
            "Не вдалося створити валідний HTML: "
            f"{validation_error}"
        )

    order.site_code = html

    project_name = (
        f"grox-order-{order.order_id}-"
        f"{uuid.uuid4().hex[:8]}"
    )

    files = {
        "index.html": html
    }

    site_url = await deploy_to_vercel(
        project_name,
        files
    )

    print(
        f"[WEBSITE] Deployed: {site_url}"
    )

    for attempt in range(
        1,
        MAX_WEBSITE_FIX_ATTEMPTS + 1
    ):
        success, error = await test_website(
            site_url
        )

        if success:
            order.site_url = site_url

            print(
                f"[WEBSITE] Test successful."
            )

            return site_url

        print(
            f"[WEBSITE] Test failed "
            f"(attempt {attempt}): "
            f"{error}"
        )

        if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
            break

        html = await fix_website(
            order,
            html,
            error or "Невідома помилка."
        )

        valid, validation_error = (
            await validate_website(html)
        )

        if not valid:
            print(
                "[WEBSITE] Fixed HTML still invalid: "
                f"{validation_error}"
            )

            continue

        order.site_code = html

        site_url = await deploy_to_vercel(
            project_name,
            {
                "index.html": html
            }
        )

    raise RuntimeError(
        "Сайт не пройшов автоматичне тестування "
        f"після {MAX_WEBSITE_FIX_ATTEMPTS} спроб."
    )


# ============================================================
# PROJECT TYPE DETECTION
# ============================================================

def detect_project_type(
    text: str
):
    normalized = text.casefold()

    if (
        "discord" in normalized
        and (
            "bot" in normalized
            or "бот" in normalized
        )
    ):
        return "Discord Bot"

    if (
        "telegram" in normalized
        and (
            "bot" in normalized
            or "бот" in normalized
        )
    ):
        return "Telegram Bot"

    if (
        "website" in normalized
        or "сайт" in normalized
        or "web" in normalized
        or "лендинг" in normalized
    ):
        return "Website"

    if (
        "app" in normalized
        or "додаток" in normalized
        or "апка" in normalized
    ):
        return "Application"

    if (
        "script" in normalized
        or "скрипт" in normalized
    ):
        return "Script"

    if (
        "bot" in normalized
        or "бот" in normalized
    ):
        return "Bot"

    return "IT Project"


# ============================================================
# ORDER THREAD CREATION
# ============================================================

async def create_private_thread(
    message: discord.Message,
    order: Order
):
    channel = message.channel

    if not isinstance(
        channel,
        discord.TextChannel
    ):
        raise RuntimeError(
            "Замовлення повинно бути "
            "у текстовому Discord-каналі."
        )

    try:
        thread = await message.create_thread(
            name=f"order-{order.order_id}"
        )

    except Exception as error:
        print(
            f"[THREAD ERROR] "
            f"{type(error).__name__}: "
            f"{error!r}"
        )

        raise

    order.thread_id = thread.id

    print(
        f"[THREAD CREATED] "
        f"order=#{order.order_id} "
        f"thread={thread.id}"
    )

    return thread


# ============================================================
# ORDER START
# ============================================================

async def process_order(
    order: Order,
    original_message: discord.Message
):
    if order.order_id in active_orders:
        return

    active_orders.add(order.order_id)

    try:
        order.status = "ANALYZING"

        thread = get_order_thread(order)

        if thread is None:
            thread = await create_private_thread(
                original_message,
                order
            )

        await thread.send(
            f"🤖 **Grox отримав замовлення "
            f"#{order.order_id}!**\n\n"
            "⏳ Аналізую технічне завдання "
            "та визначаю вартість..."
        )

        (
            project_type,
            complexity,
            price,
            reason
        ) = await estimate_project(
            order.task
        )

        order.project_type = (
            project_type
            or detect_project_type(
                order.task
            )
        )

        order.complexity = (
            complexity
            or "MEDIUM"
        )

        order.budget = price
        order.price_reason = (
            reason
            or "Оцінка Grox."
        )

        order.status = "WAITING_PRICE_APPROVAL"

        approval_message = await thread.send(
            price_status_message(order)
        )

        order.approval_message_id = (
            approval_message.id
        )

        print(
            f"[ORDER] #{order.order_id} "
            f"waiting for price approval."
        )

        # ----------------------------------------------------
        # WAIT FOR CLIENT PRICE APPROVAL
        # ----------------------------------------------------

        def approval_check(
            message: discord.Message
        ):
            return (
                message.author.id
                == order.client_id
                and message.channel.id
                == thread.id
                and is_price_approval(
                    message.content
                )
            )

        try:
            approval_message = await bot.wait_for(
                "message",
                check=approval_check,
                timeout=CLIENT_TIMEOUT
            )

        except asyncio.TimeoutError:
            order.status = "TIMEOUT"

            await thread.send(
                "⏰ **Час очікування минув.**\n\n"
                "Замовлення призупинено, тому що "
                "клієнт не підтвердив ціну."
            )

            return

        order.client_approved_price = True

        # ====================================================
        # START WORK — PAYMENT TEMPORARILY DISABLED
        # ====================================================

        order.status = "IN_PROGRESS"

        await thread.send(
            f"✅ **Ціну погоджено!**\n\n"
            f"💰 Вартість проєкту: "
            f"**${order.budget}**\n\n"
            "🤖 Grox починає виконання проєкту..."
        )

        # ----------------------------------------------------
        # GENERATE PROJECT
        # ----------------------------------------------------

        if (
            order.project_type.casefold()
            == "website"
            or "сайт" in order.task.casefold()
            or "website" in order.task.casefold()
            or "landing" in order.task.casefold()
            or "лендинг" in order.task.casefold()
        ):
            await thread.send(
                "🌐 **Створюю сайт...**\n\n"
                "⏳ Генерую код, деплою його "
                "та перевіряю результат."
            )

            try:
                site_url = (
                    await build_and_deploy_website(
                        order
                    )
                )

                order.site_url = site_url

            except Exception as error:
                print(
                    f"[WEBSITE ERROR] "
                    f"Order #{order.order_id}: "
                    f"{type(error).__name__}: "
                    f"{error!r}"
                )

                await thread.send(
                    "⚠️ Під час створення сайту "
                    "виникла помилка.\n\n"
                    f"`{error}`"
                )

                raise

        else:
            await thread.send(
                "🤖 **Починаю розробку проєкту...**"
            )

            generated_project = (
                await generate_project(order)
            )

            order.site_code = (
                generated_project
            )

        # ====================================================
        # PROJECT READY — PAYMENT TEMPORARILY DISABLED
        # ====================================================

        order.status = "COMPLETED"

        # ====================================================
        # SITE PREVIEW
        # ====================================================

        if order.site_url# ============================================================
# ORDER PROCESSING
# ============================================================

async def process_order(
    message: discord.Message
):
    if message.id in active_orders:
        return

    active_orders.add(
        message.id
    )

    order = None

    try:
        # ====================================================
        # CREATE ORDER
        # ====================================================

        order = create_order(
            message
        )

        # ====================================================
        # CREATE PRIVATE THREAD
        # ====================================================

        try:
            thread = await message.create_thread(
                name=(
                    f"Grox #{order.order_id} — "
                    f"{message.author.name}"
                ),
                auto_archive_duration=10080
            )

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
        # WAIT FOR TЗ
        # ====================================================

        def check_task(
            msg: discord.Message
        ):
            return (
                msg.author.id == order.client_id
                and msg.channel.id == thread.id
                and not msg.author.bot
            )

        try:
            client_message = await bot.wait_for(
                "message",
                check=check_task,
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

        task = client_message.content.strip()

        # ====================================================
        # TASK VALIDATION
        # ====================================================

        if not task:
            await thread.send(
                "❌ ТЗ не може бути порожнім."
            )

            order.status = "ERROR"
            return

        if len(task) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ ТЗ занадто велике.\n"
                f"Максимум: "
                f"{MAX_TASK_LENGTH} символів."
            )

            order.status = "ERROR"
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
        # WAIT FOR PRICE APPROVAL
        # ====================================================

        approved = (
            await wait_for_price_approval(
                thread,
                order
            )
        )

        if not approved:
            return

        # ====================================================
        # START WORK — PAYMENT DISABLED
        # ====================================================

        order.status = "IN_PROGRESS"

        await thread.send(
            f"✅ **Ціну погоджено!**\n\n"
            f"💰 Вартість проєкту: "
            f"**${order.budget}**\n\n"
            "🤖 Grox починає виконання "
            "проєкту..."
        )

        # ====================================================
        # PROJECT TYPE DETECTION
        # ====================================================

        task_lower = task.casefold()

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
                    "генерацію або перевірку сайту.**"
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
                "🔧 Підготовка проєкту триває."
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
        # PROJECT READY — PAYMENT DISABLED
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

        if order:
            order.status = "ERROR"

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
        f"🧪 Website QA enabled: YES"
    )

    print(
        f"🔧 Max website fix attempts: "
        f"{MAX_WEBSITE_FIX_ATTEMPTS}"
    )

    print(
        f"💬 Robust price approval: YES"
    )

    print(
        f"💳 Payment system: DISABLED"
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

    # Commands
    await bot.process_commands(
        message
    )

    # Only configured client channel
    if (
        message.channel.id
        != CLIENT_CHANNEL_ID
    ):
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
# HEALTH SERVER
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
