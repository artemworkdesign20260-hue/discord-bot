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

# Мінімальна можлива ціна автоматичної оцінки.
# Це НЕ мінімальний бюджет замовлення.
MINIMUM_PROJECT_PRICE = 50

MAX_TASK_LENGTH = int(
    os.getenv("MAX_TASK_LENGTH", "12000")
)

GEMINI_RETRIES = int(
    os.getenv("GEMINI_RETRIES", "4")
)


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

    # Спочатку бюджет невідомий.
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

        f"💰 Залишок: "
        f"**${order.remaining}**\n\n"

        f"🔒 **Фінальна передача заблокована.**\n\n"

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

    # Тепер бюджет НЕ потрібен.
    # Достатньо, щоб у повідомленні
    # була ознака IT-проєкту.

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


    await thread.send(

        f"💳 **Тестова оплата №{payment_number}**\n\n"

        f"Сума: **${amount}**\n\n"

        f"⏳ Це тестовий режим.\n\n"

        f"Для тесту адміністратор може "
        f"підтвердити оплату командою:\n"

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


    if payment_number not in (
        1,
        2
    ):

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


    # --------------------------------------------------------
    # FIRST PAYMENT
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # SECOND PAYMENT
    # --------------------------------------------------------

    if payment_number == 2:

        if not order.payment1_confirmed:

            await ctx.send(

                "❌ Не можна підтвердити "
                "другу оплату до першої."
            )

            return


        if order.status != "WAITING_FINAL_PAYMENT":

            await ctx.send(

                "❌ Проєкт ще не перейшов "
                "у стан очікування "
                "фінальної оплати."
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

                    "Gemini повернув "
                    "порожню відповідь."
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
   з реальною оцінкою, але не копіюй його автоматично.
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

            "Gemini не повернув "
            "коректну ціну."
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

            "Технічне завдання "
            "занадто велике."
        )


    prompt = f"""
Ти — професійний веб-розробник системи Grox.

Створи повністю готовий до запуску
односторінковий вебсайт за технічним
завданням клієнта.

ВАЖЛИВІ ВИМОГИ:

1. Поверни ТІЛЬКИ HTML-код.
2. Не використовуй Markdown.
3. Не використовуй ```html.
4. CSS повинен бути всередині HTML.
5. JavaScript повинен бути всередині HTML.
6. Сайт повинен бути адаптивним.
7. Дизайн повинен виглядати професійно.
8. Використовуй сучасний UI/UX.
9. Якщо клієнт не вказав кольори —
   вибери професійну кольорову схему.
10. Не додавай пояснення перед або після HTML.
11. Код повинен бути одним повним index.html.
12. Не залишай TODO.
13. Не використовуй зовнішні CSS/JS файли.
14. Сайт повинен працювати після відкриття.
15. HTML повинен мати DOCTYPE, html,
    head та body.
16. НЕ вигадуй реальних клієнтів,
    компаній, відгуків або результатів.
17. Якщо потрібні відгуки, використовуй
    нейтральні placeholder-и або познач
    їх як демонстраційні.
18. Не використовуй фальшиві testimonials
    від імені реальних людей.

Технічне завдання клієнта:

{client_task}
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

        # ----------------------------------------------------
        # CREATE ORDER WITHOUT BUDGET
        # ----------------------------------------------------

        order = create_order(
            message
        )


        # ----------------------------------------------------
        # CREATE THREAD
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # GREETING
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # WAIT FOR TЗ
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # TASK VALIDATION
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # OPTIONAL CLIENT BUDGET
        # ----------------------------------------------------

        order.client_budget = extract_budget(
            task
        )


        # ----------------------------------------------------
        # ANALYZE PROJECT
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # SAVE ESTIMATION
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # SHOW PRICE
        # ----------------------------------------------------

        await thread.send(

            price_status_message(
                order
            )
        )


        # ----------------------------------------------------
        # WAIT FOR CLIENT APPROVAL
        # ----------------------------------------------------

        approved = await wait_for_price_approval(

            thread,

            order
        )


        if not approved:

            return


        # ----------------------------------------------------
        # PRICE APPROVED
        # ----------------------------------------------------

        order.status = (
            "WAITING_DEPOSIT"
        )


        await thread.send(

            f"✅ **Ціну погоджено!**\n\n"

            f"💰 Загальна сума: "
            f"**${order.budget}**\n"

            f"💵 Передоплата: "
            f"**${order.deposit}**\n"

            f"💵 Після завершення: "
            f"**${order.remaining}**"
        )


        # ----------------------------------------------------
        # FIRST PAYMENT
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # START WORK
        # ----------------------------------------------------

        await thread.send(

            "🟢 **Передоплату підтверджено!**\n\n"

            "🤖 Grox починає виконання "
            "проєкту..."
        )


        # ----------------------------------------------------
        # PROJECT TYPE DETECTION
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # WEBSITE GENERATION
        # ----------------------------------------------------

        if website_project:

            await thread.send(

                "💻 **Генерую сайт...**"
            )


            try:

                html_code = await generate_site_code(

                    order.task
                )


            except Exception as error:

                print(
                    "========================================"
                )

                print(
                    "[WEBSITE GENERATION ERROR]"
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

                    "❌ **Не вдалося "
                    "згенерувати сайт.**\n\n"

                    "Технічна помилка записана "
                    "в Render Logs."
                )

                return


            # ------------------------------------------------
            # VERCEL
            # ------------------------------------------------

            project_name = (

                f"grox-job-"
                f"{uuid.uuid4().hex[:12]}"

            )


            await thread.send(

                "🚀 **Код готовий.**\n\n"

                "☁️ Виконую автоматичний "
                "деплой на Vercel..."
            )


            live_url = await deploy_to_vercel(

                project_name,

                html_code
            )


            if not live_url:

                await thread.send(

                    "⚠️ Код сайту згенерований, "
                    "але Vercel не повернув "
                    "адресу деплою.\n\n"

                    "Фінальна передача "
                    "залишається заблокованою."
                )

                return


            order.site_url = live_url


        # ----------------------------------------------------
        # DISCORD BOT
        # ----------------------------------------------------

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

                "🤖 **Проєкт Discord-бота "
                "підготовлений до розробки.**\n\n"

                "Для складних ботів потрібне "
                "окреме тестування та налаштування "
                "середовища."
            )


            # У старій версії Grox не мав
            # безпечного автоматичного деплою
            # Discord-ботів.
            #
            # Тому НЕ будемо брехати клієнту,
            # що бот вже запущений.


        # ----------------------------------------------------
        # OTHER PROJECT
        # ----------------------------------------------------

        else:

            await thread.send(

                "🛠️ **Виконую проєкт "
                "відповідно до ТЗ...**"
            )


        # ----------------------------------------------------
        # PROJECT READY
        # ----------------------------------------------------

        order.status = (
            "WAITING_FINAL_PAYMENT"
        )


        await thread.send(

            final_payment_message(
                order
            )
        )


        # ----------------------------------------------------
        # SITE PREVIEW
        # ----------------------------------------------------

        if order.site_url:

            await thread.send(

                f"🌐 **Попередній результат:**\n"
                f"{order.site_url}"
            )


        # ----------------------------------------------------
        # SECOND PAYMENT
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

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
        f"💰 Minimum project price: "
        f"${MINIMUM_PROJECT_PRICE}"
    )

    print(
        f"💰 Maximum project price: "
        f"${MAXIMUM_BUDGET}"
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
