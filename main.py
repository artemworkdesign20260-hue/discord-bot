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

# ------------------------------------------------------------
# PAYMENT SETTINGS
# ------------------------------------------------------------

# true = TEST MODE ONLY
# false = production mode, but requires real payment integration
TEST_PAYMENT_MODE = os.getenv(
    "TEST_PAYMENT_MODE",
    "true"
).lower() == "true"

# Discord user ID of the administrator/owner.
# Put your own Discord user ID into Render.
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# Maximum allowed order budget.
MAXIMUM_BUDGET = int(
    os.getenv("MAXIMUM_BUDGET", "10000")
)

MINIMUM_BUDGET = 200

# Maximum TЗ length.
MAX_TASK_LENGTH = int(
    os.getenv("MAX_TASK_LENGTH", "12000")
)

# Number of Gemini retries for temporary errors.
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

    print("========== GEMINI CLIENT ERROR ==========")

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

    budget: int

    deposit: int

    remaining: int

    thread_id: int = 0

    task: str = ""

    payment1_confirmed: bool = False

    payment2_confirmed: bool = False

    site_url: str | None = None

    status: str = "WAITING_DEPOSIT"


orders = {}

active_orders = set()

next_order_id = 1000


# ============================================================
# ORDER HELPERS
# ============================================================

def create_order(
    message: discord.Message,
    budget: int
):

    global next_order_id

    next_order_id += 1

    deposit = (budget + 1) // 2

    remaining = budget - deposit

    order = Order(

        order_id=next_order_id,

        discord_message_id=message.id,

        client_id=message.author.id,

        client_name=message.author.name,

        budget=budget,

        deposit=deposit,

        remaining=remaining
    )

    orders[order.order_id] = order

    return order


# ============================================================
# ORDER STATUS MESSAGES
# ============================================================

def deposit_status_message(
    order: Order
):

    return (

        f"🟡 **Замовлення #{order.order_id}**\n\n"

        f"💰 Загальна сума: **${order.budget}**\n"

        f"💵 Передоплата: **${order.deposit}**\n\n"

        f"⏳ **Статус: Очікується передоплата**\n\n"

        f"🔒 Робота ще не розпочата."
    )


def deposit_confirmed_message(
    order: Order
):

    return (

        f"🟢 **Замовлення #{order.order_id}**\n\n"

        f"💰 Загальна сума: **${order.budget}**\n"

        f"✅ Передоплата **${order.deposit}** отримана.\n\n"

        f"🛠️ **Можна починати роботу!**"
    )


def final_payment_message(
    order: Order
):

    return (

        f"🔵 **Замовлення #{order.order_id}**\n\n"

        f"🎉 Проєкт готовий!\n\n"

        f"💰 Залишок: **${order.remaining}**\n"

        f"🔒 Фінальна передача: **ЗАБЛОКОВАНА**\n\n"

        f"⏳ Очікується друга оплата."
    )


def completed_message(
    order: Order
):

    return (

        f"🟢 **Замовлення #{order.order_id} завершене!**\n\n"

        f"💰 Загальна сума: **${order.budget}**\n"

        f"✅ Передоплата отримана\n"

        f"✅ Фінальна оплата отримана\n"

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


BUDGET_PATTERN = re.compile(

    r"""
    (?:
        \$\s*(\d[\d\s,\.]*)
        |
        (\d[\d\s,\.]*)\s*\$
        |
        (\d[\d\s,\.]*)\s*(?:usd|dollars?|долар(?:ів|и)?)
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

                    .replace(
                        " ",
                        ""
                    )

                    .replace(
                        ",",
                        ""
                    )

                    .replace(
                        ".",
                        ""
                    )
                )

                return int(cleaned)

            except ValueError:

                return None

    return None


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

    if not contains_service_keyword(
        text
    ):

        return False

    budget = extract_budget(
        text
    )

    if budget is None:

        return False

    return (

        MINIMUM_BUDGET
        <= budget
        <= MAXIMUM_BUDGET
    )


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

        f"💳 **Тестова оплата**\n\n"

        f"Сума: **${amount}**\n\n"

        f"⏳ У реальному режимі тут "
        f"буде очікування підтвердження "
        f"платіжного сервісу.\n\n"

        f"Для тесту адміністратор може "
        f"підтвердити оплату командою:\n"

        f"`!confirm {order.order_id} {payment_number}`"
    )


    return True


@bot.command(
    name="confirm"
)
async def confirm_payment(
    ctx,
    order_id: int,
    payment_number: int
):

    # --------------------------------------------------------
    # ONLY ADMIN
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # ONLY TEST MODE
    # --------------------------------------------------------

    if not TEST_PAYMENT_MODE:

        await ctx.send(

            "❌ Тестове підтвердження "
            "вимкнене."
        )

        return


    # --------------------------------------------------------
    # VALIDATE PAYMENT NUMBER
    # --------------------------------------------------------

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

        if order.payment1_confirmed:

            await ctx.send(
                "ℹ️ Перша оплата вже підтверджена."
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


        if not order.site_url:

            await ctx.send(

                "❌ Проєкт ще не готовий."
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


            await
