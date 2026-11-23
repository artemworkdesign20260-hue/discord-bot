import asyncio
import signal
import ast
import sys
import os
import re
import uuid
import random
import base64
import json
from dataclasses import asdict, dataclass
from urllib.parse import quote

import aiohttp
import asyncpg
from aiohttp import web

import discord
from discord.ext import commands

from playwright.async_api import async_playwright



# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")

# Optional GitHub/Render integration for generated software bots.
# These are intentionally optional so Grox can still run for website-only work.
RENDER_API_KEY = os.getenv("RENDER_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# Optional Telegram credentials passed to generated Telegram projects.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# Token for a generated Discord bot (for example Grox Builder).
# Never hardcode it or put it into GitHub; Grox passes it to Render as an env var.
GENERATED_DISCORD_TOKEN = (
    os.getenv("GROX_BUILDER_DISCORD_TOKEN")
    or os.getenv("BUILDER_DISCORD_TOKEN")
)

# Dedicated database for Grox's own persistent order history.
# This is deliberately separate from Bot 2's DATABASE_URL.
GROX_DATABASE_URL = os.getenv("GROX_DATABASE_URL")

# Optional durable database connection for generated Bot 2 projects.
# On Render, SQLite files may be lost when an instance is replaced unless
# a persistent disk is configured. Prefer PostgreSQL via DATABASE_URL.
BOT2_DATABASE_URL = (
    os.getenv("BOT2_DATABASE_URL")
    or os.getenv("DATABASE_URL")
)
BOT2_MIN_GUILD_MEMBERS = int(os.getenv("BOT2_MIN_GUILD_MEMBERS", "500"))
BOT2_DEPLOY_WAIT_SECONDS = int(os.getenv("BOT2_DEPLOY_WAIT_SECONDS", "600"))
BOT2_HEALTH_POLL_INTERVAL = int(os.getenv("BOT2_HEALTH_POLL_INTERVAL", "15"))

CLIENT_CHANNEL_ID = os.getenv("CLIENT_CHANNEL_ID")
GROX_OWNER_ID = os.getenv("GROX_OWNER_ID", "").strip()

# ============================================================
# SECRET REDACTION
# ============================================================

_SECRET_ENV_NAME_RE = re.compile(
    r"(?:TOKEN|API_KEY|SECRET|PASSWORD|DATABASE_URL|API_HASH|PRIVATE_KEY)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}", re.IGNORECASE),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"mfa\.[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"),
)


def redact_sensitive_text(value) -> str:
    """Remove configured secrets and common credential formats from diagnostics."""
    result = str(value)
    for name, secret in os.environ.items():
        if _SECRET_ENV_NAME_RE.search(name) and secret and len(secret) >= 6:
            result = result.replace(secret, "[REDACTED]")
    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def safe_exception(error: Exception) -> str:
    """Return only the exception class; raw messages may contain credentials or user data."""
    return type(error).__name__


def safe_error_detail(error: Exception, limit: int = 1200) -> str:
    """Expose sanitized diagnostics for expected and unexpected exceptions."""
    error_type = type(error).__name__
    try:
        detail = redact_sensitive_text(str(error)).strip()
        detail = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", detail)
        # Keep diagnostics compact and prevent control characters from shaping
        # Discord messages or log output.
        detail = re.sub(r"[\r\n\t]+", " ", detail)
        detail = re.sub(r"\s+", " ", detail).strip()
    except Exception:
        return error_type
    if not detail:
        return error_type
    return f"{error_type}: {detail[:limit]}"



GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

# Groq TPM guard. Keep a safety margin because the provider counts input,
# reserved output tokens, and other requests made during the same minute.
GROQ_TPM_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "8000"))
GROQ_TPM_RESERVE = int(os.getenv("GROQ_TPM_RESERVE", "700"))
GROQ_MIN_COMPLETION_TOKENS = int(
    os.getenv("GROQ_MIN_COMPLETION_TOKENS", "256")
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
    os.getenv("MAX_REQUIREMENT_ROUNDS", "3")
)

PORT = int(
    os.getenv("PORT", "10000")
)


# ============================================================
# SECURITY / PROJECT SETTINGS
# ============================================================

MAX_TASK_LENGTH = int(
    os.getenv("MAX_TASK_LENGTH", "12000")
)

GROQ_RETRIES = int(
    os.getenv("GROQ_RETRIES", os.getenv("GEMINI_RETRIES", "4"))
)

MAX_WEBSITE_FIX_ATTEMPTS = 4

# How often the approval/payment state is checked.
STATE_CHECK_INTERVAL = 1

# Maximum number of simultaneously open orders (threads) per client.
MAX_ACTIVE_ORDERS_PER_USER = int(
    os.getenv("MAX_ACTIVE_ORDERS_PER_USER", "3")
)

# Grox is a private, owner-only developer assistant. Every Discord action
# and every deployment must originate from this single configured account.


# ============================================================
# CHECK ENVIRONMENT
# ============================================================

required_variables = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "GROQ_API_KEY": GROQ_API_KEY,
    "CLIENT_CHANNEL_ID": CLIENT_CHANNEL_ID,
    "GROX_OWNER_ID": GROX_OWNER_ID,
    "GROX_DATABASE_URL": GROX_DATABASE_URL,
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

try:
    GROX_OWNER_ID = int(GROX_OWNER_ID)
except ValueError:
    raise RuntimeError(
        "GROX_OWNER_ID повинен бути Discord ID власника (лише цифри)."
    )


# ============================================================
# GROQ CLIENT CONFIGURATION
# ============================================================

if not GROQ_API_KEY:
    raise RuntimeError("Відсутня Environment Variable: GROQ_API_KEY")

print(f"[GROQ] API configured. Model: {GROQ_MODEL}")

print("[SECURITY] Owner-only mode enabled.")


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
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

    # Generated software-bot deployment information.
    project_repo_path: str | None = None
    deployment_url: str | None = None
    render_service_id: str | None = None

    status: str = "WAITING_TZ"

    # Used to prevent stale/duplicate approval handling.
    approval_message_id: int = 0


orders: dict[int, Order] = {}
active_orders: set[int] = set()
processing_tasks: set[asyncio.Task] = set()

# Number of currently open orders per client (see MAX_ACTIVE_ORDERS_PER_USER).
active_clients: dict[int, int] = {}

# Names of Render services deployed with the shared Discord builder token.
builder_token_services: list[str] = []

db_pool: asyncpg.Pool | None = None


async def initialize_database():
    """Create Grox's own tables and restore saved order records."""
    global db_pool

    if not GROX_DATABASE_URL:
        raise RuntimeError("Відсутня Environment Variable: GROX_DATABASE_URL")

    # Neon connection strings include libpq query parameters such as
    # sslmode/channel_binding. asyncpg uses ssl=True instead.
    database_dsn = GROX_DATABASE_URL.split("?", 1)[0]
    db_pool = await asyncpg.create_pool(
        dsn=database_dsn,
        ssl=True,
        min_size=0,
        max_size=3,
        max_inactive_connection_lifetime=240,
        command_timeout=30,
    )

    async with db_pool.acquire() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS grox_orders (
                order_id BIGINT PRIMARY KEY,
                discord_message_id BIGINT NOT NULL UNIQUE,
                client_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await connection.execute(
            "CREATE SEQUENCE IF NOT EXISTS grox_order_id_seq START WITH 1000"
        )
        # Multiple Render instances can start during a deploy. Serialize
        # sequence synchronization and never move it behind an ID already used.
        async with connection.transaction():
            await connection.fetchval(
                "SELECT pg_advisory_xact_lock(8472011, 1)"
            )
            await connection.fetchval(
                """
                SELECT setval(
                    'grox_order_id_seq',
                    GREATEST(
                        (SELECT last_value FROM grox_order_id_seq),
                        COALESCE((SELECT MAX(order_id) FROM grox_orders), 1000)
                    ),
                    true
                )
                """
            )
        rows = await connection.fetch(
            "SELECT payload FROM grox_orders ORDER BY order_id"
        )

    restored = 0
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        order = Order(**payload)
        orders[order.order_id] = order
        restored += 1

    # In-memory Discord listeners/tasks cannot survive a Render restart.
    # Mark unfinished orders as interrupted instead of leaving them falsely
    # looking active forever after the process comes back online.
    interrupted = 0
    for order in orders.values():
        if order.status in {"WAITING_TZ", "IN_PROGRESS"}:
            order.status = "INTERRUPTED"
            await save_order(order)
            interrupted += 1

    print(
        f"[DATABASE] Neon connected; restored {restored} saved order(s); "
        f"marked {interrupted} interrupted order(s)."
    )


async def close_database():
    global db_pool
    if db_pool is not None:
        await db_pool.close()
        db_pool = None


async def save_order(order: Order):
    """Persist a complete order snapshot; fail loudly if storage is unavailable."""
    if db_pool is None:
        raise RuntimeError("Grox database is not initialized.")

    payload = json.dumps(asdict(order), ensure_ascii=False)
    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO grox_orders (
                order_id, discord_message_id, client_id, status, payload, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
            ON CONFLICT (order_id) DO UPDATE SET
                discord_message_id = EXCLUDED.discord_message_id,
                client_id = EXCLUDED.client_id,
                status = EXCLUDED.status,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            order.order_id,
            order.discord_message_id,
            order.client_id,
            order.status,
            payload,
        )


# ============================================================
# ORDER HELPERS
# ============================================================

async def create_order(message: discord.Message) -> Order | None:
    """Atomically allocate an order ID and reject duplicate Discord messages."""
    if db_pool is None:
        raise RuntimeError("Grox database is not initialized.")

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            # Use the same advisory lock as startup sequence synchronization.
            # This prevents a concurrent startup from rewinding the sequence
            # between nextval() and a new order reservation.
            await connection.fetchval(
                "SELECT pg_advisory_xact_lock(8472011, 1)"
            )
            order_id = await connection.fetchval(
                "SELECT nextval('grox_order_id_seq')"
            )
            order = Order(
                order_id=order_id,
                discord_message_id=message.id,
                client_id=message.author.id,
                client_name=message.author.name,
            )
            payload = json.dumps(asdict(order), ensure_ascii=False)
            inserted_id = await connection.fetchval(
                """
                INSERT INTO grox_orders (
                    order_id, discord_message_id, client_id, status, payload, updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
                ON CONFLICT (discord_message_id) DO NOTHING
                RETURNING order_id
                """,
                order.order_id,
                order.discord_message_id,
                order.client_id,
                order.status,
                payload,
            )

    if inserted_id is None:
        print(
            f"[ORDER DUPLICATE] message={message.id} already exists in database; "
            "skipping duplicate event."
        )
        return None

    orders[order.order_id] = order
    print(
        f"[ORDER CREATED] #{order.order_id} "
        f"client={order.client_id} message={message.id}"
    )
    return order


def get_order_thread(order: Order):
    if not order.thread_id:
        return None

    channel = bot.get_channel(order.thread_id)

    if isinstance(channel, discord.Thread):
        return channel

    return None


async def send_chunks(channel, text: str, limit: int = 1900):
    """
    Discord rejects messages longer than 2000 characters.
    Sends long text as several consecutive messages.
    """
    text = (text or "").strip() or "…"

    while text:
        if len(text) <= limit:
            await channel.send(text)
            break

        cut = text.rfind("\n", 0, limit)

        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)

        if cut < limit // 2:
            cut = limit

        await channel.send(text[:cut].rstrip())
        text = text[cut:].lstrip()


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

def completed_message(order: Order):
    return (
        f"🟢 **Замовлення #{order.order_id} завершене!**\n\n"
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
    "додатку",
    "додатки",
    "додатків",

    "script",
    "скрипт",

    "development",
    "розробк",

    "landing",
    "лендинг",

    "discord bot",
    "discord бот",
]


def _keyword_regex(keyword: str) -> str:
    """
    Latin keywords must match as WHOLE words (optionally plural):
    "bot" must not match "both" or "robot".
    Cyrillic keywords match from the START of a word, so declensions work
    ("бот" -> "ботів", "сайт" -> "сайту") but "робота" is NOT a match.
    """
    keyword = keyword.casefold().strip()
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")

    if re.search(r"[а-яіїєґё]", keyword):
        return rf"(?<!\w){escaped}"

    return rf"(?<!\w){escaped}s?(?!\w)"


def has_keyword(text: str, keywords) -> bool:
    text = (text or "").casefold()

    return any(
        re.search(_keyword_regex(keyword), text)
        for keyword in keywords
    )


def contains_service_keyword(text: str) -> bool:
    return has_keyword(text, KEYWORDS)


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


def _parse_amount(raw: str):
    """
    "10.50" -> 10, "1,000" -> 1000, "1 000" -> 1000, "1.000,50" -> 1000.
    The old code removed every '.' and ',', so "$10.50" became 1050.
    """
    s = raw.replace(" ", "").strip(".,")

    if not s:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")

    try:
        return int(float(s))
    except ValueError:
        return None


def extract_budget(text: str):
    match = BUDGET_PATTERN.search(text)

    if not match:
        return None

    for group in match.groups():
        if group:
            return _parse_amount(group)

    return None


# ============================================================
# PAYMENT TEST SYSTEM
# ============================================================



# ============================================================
# ADMIN PAYMENT CONFIRMATION
# ============================================================


# ============================================================
# GROQ REQUEST
# ============================================================

GROQ_REQUEST_LOCK = asyncio.Lock()


def estimate_groq_prompt_tokens(prompt: str) -> int:
    """
    Conservative local estimate without another tokenizer dependency.
    Non-ASCII text is budgeted more heavily; ASCII code/punctuation also
    gets a conservative allowance. This is a guard, not an exact tokenizer.
    """
    non_ascii = sum(1 for char in prompt if ord(char) > 127)
    ascii_chars = len(prompt) - non_ascii
    # Calibrated against token counts reported by Groq in live logs.
    # The old character-per-token estimate was too high and left only
    # 812 completion tokens for a complete multi-file project.
    return max(200, int(non_ascii * 0.65 + ascii_chars * 0.4) + 200)


async def _groq_request_unlocked(
    prompt: str,
    max_output_tokens: int,
    return_metadata: bool = False,
):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Порожній запит до Groq.")

    last_error = None
    total_attempts = max(1, GROQ_RETRIES + 1)

    # Leave headroom for estimation error and concurrent requests using the
    # same organization-level TPM quota.
    estimated_input_tokens = estimate_groq_prompt_tokens(prompt)
    available_output_tokens = (
        GROQ_TPM_LIMIT - GROQ_TPM_RESERVE - estimated_input_tokens
    )
    if available_output_tokens < GROQ_MIN_COMPLETION_TOKENS:
        raise RuntimeError(
            "Запит до Groq завеликий для поточного ліміту TPM. "
            "Потрібно скоротити вхідний контекст або розбити завдання на частини. "
            f"Оцінка вхідних токенів: {estimated_input_tokens}; "
            f"ліміт: {GROQ_TPM_LIMIT}; резерв: {GROQ_TPM_RESERVE}."
        )

    current_max_output_tokens = min(
        max(GROQ_MIN_COMPLETION_TOKENS, int(max_output_tokens)),
        available_output_tokens,
    )
    if current_max_output_tokens < max_output_tokens:
        print(
            f"[GROQ] Output cap adjusted: requested={max_output_tokens}, "
            f"using={current_max_output_tokens}, "
            f"estimated_input={estimated_input_tokens}, "
            f"TPM_limit={GROQ_TPM_LIMIT}"
        )

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": current_max_output_tokens,
    }
    timeout = aiohttp.ClientTimeout(total=CLIENT_TIMEOUT)

    for attempt in range(1, total_attempts + 1):
        try:
            print(
                f"[GROQ] Attempt {attempt}/{total_attempts} "
                f"model={GROQ_MODEL} "
                f"max_completion_tokens={payload['max_completion_tokens']}"
            )

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response_text = await response.text()
                    try:
                        data = json.loads(response_text)
                    except json.JSONDecodeError:
                        data = {}

                    if response.status >= 400:
                        api_error = data.get("error") if isinstance(data, dict) else None
                        if isinstance(api_error, dict):
                            error_message = api_error.get("message") or response_text
                        elif isinstance(api_error, str):
                            error_message = api_error
                        else:
                            error_message = response_text or "порожня відповідь API"
                        error_message = redact_sensitive_text(str(error_message))
                        error_message = re.sub(
                            r"(?i)(bearer\s+)[^\s]+",
                            r"\1[REDACTED]",
                            error_message,
                        )
                        raise RuntimeError(
                            f"Groq API HTTP {response.status}: {error_message[:500]}"
                        )

                    if not isinstance(data, dict):
                        raise RuntimeError("Groq API повернув JSON неочікуваного формату.")

                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        raise RuntimeError("Groq API не повернув список choices у відповіді.")

                    response_choice = choices[0]
                    response_message = response_choice.get("message")
                    content = (
                        response_message.get("content")
                        if isinstance(response_message, dict)
                        else None
                    )
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError("Groq повернув порожню або некоректну відповідь.")

                    usage = data.get("usage")
                    if isinstance(usage, dict):
                        print(
                            "[GROQ] Usage: "
                            f"prompt={usage.get('prompt_tokens', 'unknown')} "
                            f"completion={usage.get('completion_tokens', 'unknown')} "
                            f"total={usage.get('total_tokens', 'unknown')}"
                        )
                    finish_reason = response_choice.get("finish_reason")
                    if finish_reason == "length":
                        print(
                            "[GROQ] WARNING: response reached the output token limit; "
                            "the generated content may be incomplete."
                        )
                        # Callers that do not explicitly handle response metadata
                        # must never consume a truncated answer (notably HTML,
                        # requirement analysis, website fixes, and chat replies).
                        if not return_metadata:
                            raise RuntimeError(
                                "Groq обрізав відповідь за лімітом токенів; "
                                "неповний результат відхилено."
                            )

                    if return_metadata:
                        return {
                            "content": content.strip(),
                            "finish_reason": finish_reason,
                            "usage": usage if isinstance(usage, dict) else {},
                        }
                    return content.strip()

        except Exception as error:
            last_error = error
            print(
                f"[GROQ ERROR] Attempt {attempt}/{total_attempts}: "
                f"{safe_error_detail(error)}"
            )
            error_text = str(error).lower()

            # A 413 from Groq can mean the reserved completion budget would
            # exceed TPM. Reduce the output reservation before retrying.
            if "413" in error_text and payload["max_completion_tokens"] > GROQ_MIN_COMPLETION_TOKENS:
                requested_match = re.search(r"Requested\s+(\d+)", str(error), re.IGNORECASE)
                limit_match = re.search(r"Limit\s+(\d+)", str(error), re.IGNORECASE)
                if requested_match and limit_match:
                    overage = max(
                        1,
                        int(requested_match.group(1)) - int(limit_match.group(1))
                    )
                    reduced = payload["max_completion_tokens"] - overage - 300
                else:
                    reduced = int(payload["max_completion_tokens"] * 0.65)

                payload["max_completion_tokens"] = max(
                    GROQ_MIN_COMPLETION_TOKENS,
                    min(payload["max_completion_tokens"] - 1, reduced),
                )
                print(
                    "[GROQ] HTTP 413: reduced max_completion_tokens to "
                    f"{payload['max_completion_tokens']} and will retry."
                )

            # asyncio.TimeoutError and aiohttp network errors often have an
            # EMPTY message, so the text check below cannot recognise them.
            temporary = isinstance(
                error,
                (asyncio.TimeoutError, aiohttp.ClientError)
            ) or any(code in error_text for code in (
                "413", "429", "rate limit", "resource exhausted", "503",
                "unavailable", "temporarily", "deadline", "timeout",
                "overloaded", "service unavailable", "502", "504",
            ))
            if not temporary:
                print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")
                raise
            if attempt >= total_attempts:
                break
            delay = min(60, (2 ** (attempt - 1)) + random.uniform(0.5, 2.0))
            retry_after_match = re.search(
                r"(?:try again in|retry after)\s*([0-9]+(?:\.[0-9]+)?)\s*s",
                str(error),
                re.IGNORECASE,
            )
            if retry_after_match:
                provider_delay = float(retry_after_match.group(1))
                delay = max(delay, provider_delay + random.uniform(0.25, 0.75))
            delay = min(120, delay)
            print(f"[GROQ] Тимчасова помилка. Повтор через {delay:.1f}s...")
            await asyncio.sleep(delay)

    last_error_detail = (
        safe_error_detail(last_error)
        if last_error is not None
        else "невідома помилка"
    )
    raise RuntimeError(
        f"Groq тимчасово недоступний після {total_attempts} спроб. "
        f"Остання помилка: {last_error_detail}"
    )


async def groq_request(
    prompt: str,
    max_output_tokens: int,
    return_metadata: bool = False,
):
    """Serialize requests and retries against the shared organization TPM quota."""
    async with GROQ_REQUEST_LOCK:
        return await _groq_request_unlocked(
            prompt,
            max_output_tokens,
            return_metadata=return_metadata,
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

Постав уточнювальне питання ТІЛЬКИ якщо без відповіді неможливо почати роботу.
Не запитуй необов'язкові побажання, кольори, назви, дрібні деталі або те,
для чого можна безпечно вибрати стандартне рішення.
Для необов'язкових деталей самостійно застосуй прості професійні значення.
Максимум одне коротке питання за раунд. Не повторюй уже надану інформацію.
Якщо завдання зрозуміле хоча б на базовому рівні — сформуй ТЗ і починай роботу.

ВАЖЛИВО ПРО СЕКРЕТИ ТА ІНТЕГРАЦІЇ:
- НІКОЛИ не проси клієнта надсилати GitHub PAT, GitHub token, API key,
  пароль, database URL, .env або будь-який інший секрет у Discord чи чат.
- Інтеграції Grox уже налаштовуються власником через змінні середовища сервера.
  Для GitHub використовуй налаштований GITHUB_TOKEN автоматично.
- Якщо потрібного секрету немає в серверній конфігурації, не проси його у клієнта:
  повідом про внутрішню помилку конфігурації власнику Grox окремо.
- Наявність або відсутність секретів НЕ є уточненням до технічного завдання клієнта.

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

    result = await groq_request(
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

    ready = bool(
        ready_match
        and ready_match.group(1).upper() == "YES"
    )
    question = question_match.group(1).strip() if question_match else ""
    final_tz = final_match.group(1).strip() if final_match else ""

    # Never let the requirements model ask a client to disclose credentials.
    # Integrations are configured by the owner in the server environment.
    credential_request = re.search(
        r"(?i)\b(?:github\s*)?(?:pat|token|api\s*key|access\s*key|"
        r"парол\w*|токен\w*|ключ\w*|секрет\w*|"
        r"database\s*url|\.env)\b",
        question
    )
    if credential_request:
        if GITHUB_TOKEN:
            # The credential is already configured server-side; it is not a
            # client requirement. Continue using the provided task description.
            ready = True
            final_tz = final_tz or conversation
        else:
            # Do not expose a request for secrets to the client.
            ready = False
            question = (
                "Внутрішня конфігурація інтеграції потребує перевірки "
                "власником Grox. Будь ласка, продовжуйте описувати функції проєкту."
            )

    return {
        "ready": ready,
        "question": question,
        "final_tz": final_tz,
    }


def requirement_control_action(text: str) -> str | None:
    """Recognise owner control commands so they are never treated as task answers."""
    value = (text or "").strip().casefold()
    compact = re.sub(r"[.!?\s]+", " ", value).strip()

    if value in {"!cancel", "!stop", "!abort"} or compact in {
        "скасуй замовлення",
        "зупини опитування",
        "припини уточнення",
        "зупини уточнення",
        "стоп",
        "скасуй",
    }:
        return "cancel"

    if value in {"!skip", "!continue"} or compact in {
        "без уточнень",
        "продовжуй без уточнень",
        "пропусти уточнення",
        "досить питань",
        "починай з того що є",
    }:
        return "skip"

    # Reserve command-like messages for future owner controls. Never feed them
    # to the requirements model as if they were part of the client's task.
    if value.startswith("!grox ") or value.startswith("!"):
        return "unknown"

    return None


async def discover_requirements(
    order: Order,
    thread: discord.Thread,
    first_message: str
):
    conversation = (
        f"Клієнт: {first_message.strip()}"
    )
    order.task = conversation
    await save_order(order)

    await thread.send(
        "🧠 **Добре, спочатку розберімося із завданням.**\n\n"
        "Я поставлю кілька коротких уточнень, якщо вони будуть потрібні. "
        "Після цього сформую фінальне ТЗ і перевірю, чи достатньо даних для виконання."
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

        await send_chunks(
            thread,
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

        control_action = requirement_control_action(answer)
        if control_action == "cancel":
            await thread.send(
                "🛑 **Уточнення зупинено.** Замовлення скасовано; "
                "це повідомлення не було додане до технічного завдання."
            )
            return None

        if control_action == "skip":
            await thread.send(
                "⏭️ **Уточнення пропущено.** Продовжую роботу "
                "з інформацією, яку ви вже надали."
            )
            return conversation

        if control_action == "unknown":
            await thread.send(
                "⚙️ Це схоже на команду, а не на відповідь. "
                "Доступні команди: !cancel — зупинити замовлення; "
                "!skip — продовжити без наступних уточнень."
            )
            continue

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
        order.task = conversation
        await save_order(order)

        await thread.send(
            "✅ **Зрозумів.**"
        )

    # The clarification budget is deliberately small. If Groq still asks
    # for optional details, build from the information already supplied.
    final_analysis = await analyze_requirements(
        conversation
    )

    if final_analysis["ready"] and final_analysis["final_tz"]:
        return final_analysis["final_tz"]

    await thread.send(
        "⚠️ Не вдалося сформувати достатньо повне технічне завдання "
        "після всіх уточнень. Я не починатиму створення навмання.\n\n"
        "Будь ласка, створіть нове замовлення та опишіть бажаний результат детальніше."
    )
    return None



# ============================================================
# HTML OUTPUT CLEANING
# ============================================================

def clean_html_output(text: str) -> str:
    """
    The model sometimes wraps HTML in ```html fences or adds explanations.
    Returns only the HTML document; raises if no HTML is present.
    """
    text = (text or "").strip()

    fenced = re.search(
        r"```(?:html)?[ \t]*\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE
    )

    if fenced:
        text = fenced.group(1).strip()
    else:
        text = re.sub(r"^```(?:html)?[ \t]*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```\s*$", "", text)

    start = re.search(r"<!doctype\s+html|<html", text, re.IGNORECASE)

    if start:
        text = text[start.start():]

    end = text.lower().rfind("</html>")

    if end == -1:
        raise RuntimeError(
            "Модель повернула неповний HTML: відсутній закривальний тег </html>."
        )

    text = text[:end + len("</html>")]
    text = text.strip()

    if "<html" not in text.lower() or "<body" not in text.lower():
        raise RuntimeError(
            "Модель не повернула коректний HTML-документ."
        )

    return text


# ============================================================
# GROQ WEBSITE GENERATION
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

    result = await groq_request(
        prompt,
        max_output_tokens=30000
    )

    return clean_html_output(result)


# ============================================================
# GROQ WEBSITE FIX
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

    result = await groq_request(
        prompt,
        max_output_tokens=30000
    )

    return clean_html_output(result)



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

    result = await groq_request(
        prompt,
        max_output_tokens=30000
    )

    return clean_html_output(result)


async def revise_and_test_website(
    order: Order,
    thread: discord.Thread,
    html_code: str,
    client_task: str | None = None,
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

        if qa_result.get("infra_error"):
            await thread.send(
                "⚠️ **QA-середовище не змогло перевірити оновлений сайт.**\\n\\n"
                "Я не буду переписувати код через помилку браузера або тестового середовища."
            )
            return False

        if qa_result["success"]:
            order.site_url = live_url
            order.site_code = html_code
            await save_order(order)
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
                client_task or order.task
            )
        except Exception as error:
            print(
                f"[REVISION FIX ERROR] "
                f"{safe_exception(error)}"
            )
            print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")
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

    return await groq_request(
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
            f"message_length={len(text)}"
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

                success = await revise_and_test_website(
                    order,
                    thread,
                    revised_html,
                    client_task=updated_task,
                )

                if success:
                    # Commit the task history only after the revised site has
                    # passed QA and its new URL/code have been saved.
                    order.task = updated_task
                    await save_order(order)
                else:
                    await thread.send(
                        "⚠️ Правку я зрозумів, але не буду "
                        "вважати її готовою, поки сайт не пройде "
                        "перевірку."
                    )

            except Exception as error:
                print(
                    f"[CLIENT REVISION ERROR] "
                    f"{safe_exception(error)}"
                )
                print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")
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
            await send_chunks(thread, f"🤖 {reply}")

        except Exception as error:
            print(
                f"[CLIENT CHAT ERROR] "
                f"{safe_exception(error)}"
            )
            print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")
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
    if not VERCEL_TOKEN:
        raise RuntimeError(
            "Для публікації сайтів потрібен VERCEL_TOKEN. "
            "Додай його в Environment Variables Render."
        )

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

                if response.status not in (200, 201) or not isinstance(data, dict):
                    print(
                        f"[VERCEL ERROR] HTTP {response.status}; response body omitted."
                    )
                    return None

                deployment_id = data.get("id") or data.get("uid")
                deployment_url = data.get("url")
                if not isinstance(deployment_url, str) or not deployment_url.strip():
                    print("[VERCEL ERROR] Create response did not include a deployment URL.")
                    return None

                if not deployment_url.startswith("http://") and not deployment_url.startswith("https://"):
                    deployment_url = "https://" + deployment_url

                # A successful POST only queues a deployment. Wait for Vercel's
                # explicit READY state before the caller starts browser QA.
                ready_state = str(data.get("readyState") or "").upper()
                if ready_state == "READY":
                    print(f"[VERCEL] Deployment READY: {deployment_url}")
                    return deployment_url

                if not deployment_id:
                    print("[VERCEL ERROR] Deployment ID missing; readiness cannot be verified.")
                    return None

                deadline = asyncio.get_running_loop().time() + 600
                deployment_endpoint = (
                    "https://api.vercel.com/v13/deployments/"
                    + quote(str(deployment_id), safe="")
                )
                while asyncio.get_running_loop().time() < deadline:
                    async with session.get(
                        deployment_endpoint,
                        headers=headers,
                    ) as status_response:
                        if status_response.status >= 400:
                            print(
                                f"[VERCEL ERROR] Readiness poll HTTP {status_response.status}; "
                                "response body omitted."
                            )
                            return None
                        try:
                            status_data = await status_response.json(content_type=None)
                        except Exception:
                            status_data = {}

                    if not isinstance(status_data, dict):
                        print("[VERCEL ERROR] Readiness poll returned an unexpected response.")
                        return None

                    ready_state = str(status_data.get("readyState") or "").upper()
                    if ready_state == "READY":
                        print(f"[VERCEL] Deployment READY: {deployment_url}")
                        return deployment_url
                    if ready_state in {"ERROR", "CANCELED", "CANCELLED"}:
                        print(f"[VERCEL ERROR] Deployment ended with state {ready_state}.")
                        return None

                    await asyncio.sleep(3)

                print("[VERCEL ERROR] Timed out waiting for deployment READY state.")
                return None

    except Exception as error:
        print(
            f"[VERCEL EXCEPTION] {type(error).__name__}; details omitted."
        )

        print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")

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
                    location = getattr(msg, "location", None) or {}

                    # A missing favicon is not a site defect.
                    if "favicon" in str(
                        location.get("url", "")
                    ).lower():
                        return

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
                    wait_until="domcontentloaded",
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

            elif response.status in (401, 403):
                await browser.close()
                return {
                    "success": False,
                    "infra_error": True,
                    "report": (
                        f"Vercel preview повернув HTTP {response.status}. "
                        "Можливий Deployment Protection або обмеження доступу. "
                        "Це не доказ помилки в HTML; перевір налаштування доступу Vercel."
                    ),
                }

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
            else:
                visible_text = (await page.locator("body").inner_text()).strip()
                meaningful_elements = await page.locator(
                    "h1, h2, h3, p, a, button, input, textarea, select, img, "
                    "video, canvas, form, main, article, section"
                ).count()
                if not visible_text and meaningful_elements == 0:
                    errors.append(
                        "Сторінка порожня: немає видимого тексту або змістовних елементів."
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
            # INSPECT BUTTONS WITHOUT CLICKING
            #
            # Clicking arbitrary buttons during QA can submit forms,
            # purchase items, send messages, delete data, or trigger
            # external side effects. Only inspect their presence and
            # accessibility; never activate them automatically.
            # ------------------------------------------------

            visible_button_count = 0
            for index in range(min(button_count, 100)):
                try:
                    button = buttons.nth(index)
                    if await button.is_visible():
                        visible_button_count += 1
                        if await button.is_disabled():
                            warnings.append(
                                f"Кнопка #{index + 1} вимкнена."
                            )
                except Exception as error:
                    warnings.append(
                        f"Не вдалося перевірити кнопку #{index + 1}: {error}"
                    )

            print(
                f"[QA] Visible buttons inspected without activation: "
                f"{visible_button_count}"
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
                        f"{safe_exception(error)}"
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
                        f"{safe_exception(error)}"
                    )

            # ------------------------------------------------
            # JAVASCRIPT CONSOLE ERRORS
            #
            # Collected after read-only inspection; no page controls are activated.
            # ------------------------------------------------

            for console_error in dict.fromkeys(console_errors):
                errors.append(
                    f"Console error: {console_error}"
                )

            # ------------------------------------------------
            # FAILED REQUESTS
            # ------------------------------------------------

            for failed_request in dict.fromkeys(failed_requests):
                warnings.append(
                    f"Неуспішний network request: "
                    f"{failed_request}"
                )

            await browser.close()

    except Exception as error:
        print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")

        return {
            "success": False,
            "infra_error": True,
            "report": (
                "QA-система не змогла завершити перевірку через внутрішню "
                "помилку браузера або тестового середовища. Код сайту не слід "
                "змінювати на підставі цього результату.\n"
                f"Помилка: {type(error).__name__}."
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
        "Кнопки автоматично не натискалися (безпечна read-only перевірка).",
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
            "\n✅ Базову перевірку завантаження та JavaScript пройдено."
            "\nℹ️ Функціональність кожної кнопки й форми автоматично не підтверджена."
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
        f"[QA SUMMARY] success={success} errors={len(errors)} warnings={len(warnings)}"
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
            "JavaScript, доступність кнопок, посилання "
            "та структуру форм без натискання елементів."
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

        # A broken Playwright/browser environment is not evidence that the
        # generated website is defective. Never feed infrastructure failures
        # into the LLM repair loop or redeploy unchanged HTML.
        if qa_result.get("infra_error"):
            await thread.send(
                "⚠️ **Автоматична QA-перевірка не завершилася через помилку "
                "тестового середовища.**\n\n"
                "Я не буду змінювати код сайту навмання або повторно розгортати "
                "той самий HTML. Перевір налаштування Playwright/Chromium у Grox."
            )
            return False

        if qa_result["success"]:
            await thread.send(
                "✅ **Базову QA-перевірку пройдено!**\n\n"
                "Сайт відкривається, критичних JavaScript-помилок "
                "не знайдено.\n\n"
                "ℹ️ Це перевірка завантаження та структури, а не повний "
                "ручний тест кожної кнопки, форми й бізнес-функції."
            )

            order.site_url = live_url
            order.site_code = html_code
            await save_order(order)

            return True

        # ----------------------------------------------------
        # FAILED
        # ----------------------------------------------------

        await thread.send(
            f"❌ **QA знайшов проблеми.**\n\n"
            f"🔧 Grox автоматично виправляє "
            f"сайт через Groq.\n\n"
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
        # GROQ FIX
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
                safe_error_detail(error)
            )

            print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")

            if attempt >= MAX_WEBSITE_FIX_ATTEMPTS:
                await thread.send(
                    "❌ Groq не зміг виправити "
                    "сайт після максимальної "
                    "кількості спроб.\n\n"
                    "Фінальна передача заблокована."
                )
                return False

            await thread.send(
                "⚠️ Groq не зміг виконати "
                "виправлення на цій спробі.\n\n"
                f"🔄 Повторюю. Наступна спроба: "
                f"{attempt + 1}/{MAX_WEBSITE_FIX_ATTEMPTS}"
            )

            continue

    return False


# ============================================================
# WAIT FOR PAYMENT CONFIRMATION
# ============================================================


# ============================================================
# SOFTWARE BOT GENERATION + GITHUB + RENDER
# ============================================================

def is_bot2_project(task: str) -> bool:
    """Recognize the dedicated second Discord lead-finder project."""
    normalized = re.sub(r"\s+", " ", task.casefold()).strip()
    patterns = (
        r"\bbot\s*[-#]?\s*2\b",
        r"\bбот\s*[-#]?\s*2\b",
        r"\bsecond\s+discord\s+bot\b",
        r"\bдругий\s+discord\s+бот\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def is_software_bot_project(task: str) -> bool:
    # Whole-word matching: the old substring check treated "робота",
    # "робот", "both" etc. as "бот"/"bot".
    bot_words = (
        "bot", "бот", "discord bot", "discord бот",
        "telegram bot", "telegram бот", "телеграм бот",
    )

    return is_bot2_project(task) or has_keyword(task, bot_words)


def detect_bot_platform(task: str) -> str:
    # Bot 2 is explicitly Discord-only, even if the description mentions
    # other platforms while explaining what must NOT be supported.
    if is_bot2_project(task):
        return "Discord"

    normalized = task.casefold()

    if any(word in normalized for word in ("telegram", "телеграм")):
        return "Telegram"

    if "discord" in normalized:
        return "Discord"

    return "Software bot"


def bot2_project_spec() -> str:
    return f"""
SPECIAL PROJECT: BOT 2 — DISCORD-ONLY LEAD-FINDING BOT
This is the user's existing Bot 2 project, not a generic customer bot.
Implement the complete working application, not a mockup or website.

MANDATORY REQUIREMENTS:
1. Discord ONLY. Do not implement Telegram, Slack, X, web scraping of other
   platforms, or cross-platform features.
2. The bot may inspect messages ONLY in Discord guilds where it has been
   explicitly invited and has the required Discord permissions/intents.
   Do not claim it can search every public Discord server or discover servers
   it has not joined. Never bypass Discord access controls or rate limits.
3. Consider only guilds with at least {BOT2_MIN_GUILD_MEMBERS} members.
   Make this configurable through BOT2_MIN_GUILD_MEMBERS (default 500).
4. Give European communities priority when reliable server metadata or
   moderator-provided configuration identifies them. Do not guess a user's
   nationality or location from message content.
5. Detect genuine requests for websites, Discord bots, applications, and
   software development. Extract the request, category, stated budget (if
   any), message link, guild/channel, timestamp, and lead status.
6. Deduplicate repeated opportunities and filter unrelated chatter, ads,
   scams, and already-reviewed leads. Do not invent budgets or claim a lead
   is confirmed when the message is ambiguous.
7. Send candidate leads to configured moderators for human review. No cold
   DMs, unsolicited outreach, automatic replies to prospects, or messaging
   clients without explicit moderator approval.
8. Support multiple moderator roles/users (including a large moderator team).
   Restrict approval/rejection/configuration commands to authorized moderators
   or guild administrators. Never treat every member as a moderator.
9. Persist leads, review decisions, and processing checkpoints. Support
   PostgreSQL through DATABASE_URL (or BOT2_DATABASE_URL) for production;
   SQLite may be used for local development. Clearly warn if deployed without
   durable database storage. Do not pretend ephemeral local files are durable.
10. Handle reconnects, Discord rate limits, transient API failures, duplicate
    events, and graceful shutdown. Bound background tasks and log failures.
11. Include a /health endpoint returning JSON with at least:
    {{"status":"ok","discord_ready":true}} when Discord is connected and
    ready; return discord_ready=false before the bot is ready.
12. Provide complete main.py, requirements.txt, and README.md. README must
    list every required environment variable and explain Discord Developer
    Portal intents, required bot permissions, server installation, moderator
    setup, database setup, and Render deployment.
13. Include automated tests or a self-test for lead classification,
    deduplication, minimum member filtering, and moderator authorization.
14. Do not leave TODOs, placeholder functions, fake data, or nonfunctional
    buttons. Do not send any message to prospects automatically.

The application must start with `python main.py`, read its bot token only
from DISCORD_TOKEN, and never print or commit secrets.
"""


def validate_generated_project(
    files: dict[str, str],
    bot2: bool = False,
    require_discord: bool = False,
) -> list[str]:
    """Validate every generated Python file before any file is published."""
    errors: list[str] = []
    reported: set[str] = set()

    def add_error(message: str):
        if message not in reported:
            reported.add(message)
            errors.append(message)

    for required in ("main.py", "requirements.txt", "README.md"):
        if not isinstance(files.get(required), str) or not files[required].strip():
            add_error(f"Відсутній або порожній файл {required}.")

    main_code = files.get("main.py", "")
    python_sources = {
        name: content
        for name, content in files.items()
        if isinstance(name, str)
        and name.lower().endswith(".py")
        and isinstance(content, str)
    }

    forbidden_secrets = {
        "GITHUB_TOKEN",
        "RENDER_API_KEY",
        "VERCEL_TOKEN",
        "GROX_DATABASE_URL",
    }
    dangerous_os_calls = {
        "system", "popen",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
    }

    requirement_names: set[str] = set()
    for requirement_line in files.get("requirements.txt", "").splitlines():
        clean_line = requirement_line.split("#", 1)[0].strip()
        if not clean_line or clean_line.startswith("-"):
            continue
        if " @ " in clean_line:
            clean_line = clean_line.split(" @ ", 1)[0].strip()
        package_name = re.split(r"[<>=!~;\\[]", clean_line, maxsplit=1)[0]
        if package_name:
            requirement_names.add(package_name.casefold().replace("_", "-").replace(".", "-"))

    known_import_distributions = {
        "aiohttp": {"aiohttp"},
        "asyncpg": {"asyncpg"},
        "discord": {"discord-py", "py-cord"},
        "PIL": {"pillow"},
        "cv2": {"opencv-python", "opencv-python-headless"},
        "yaml": {"pyyaml"},
        "bs4": {"beautifulsoup4"},
        "dotenv": {"python-dotenv"},
        "dateutil": {"python-dateutil"},
        "jwt": {"pyjwt"},
        "sklearn": {"scikit-learn"},
        "telegram": {"python-telegram-bot", "aiogram", "telethon"},
        "google": {"google-genai", "google-generativeai", "google-api-python-client"},
        "openai": {"openai"},
        "requests": {"requests"},
        "flask": {"flask"},
        "fastapi": {"fastapi"},
        "uvicorn": {"uvicorn"},
        "sqlalchemy": {"sqlalchemy"},
        "psycopg": {"psycopg", "psycopg2", "psycopg2-binary"},
        "psycopg2": {"psycopg2", "psycopg2-binary"},
        "redis": {"redis"},
        "playwright": {"playwright"},
        "numpy": {"numpy"},
        "pandas": {"pandas"},
        "pydantic": {"pydantic"},
        "stripe": {"stripe"},
        "boto3": {"boto3"},
        "supabase": {"supabase"},
        "firebase_admin": {"firebase-admin"},
        "qrcode": {"qrcode"},
        "cryptography": {"cryptography"},
        "websockets": {"websockets"},
    }
    standard_library = set(getattr(sys, "stdlib_module_names", ()))
    local_modules = {
        os.path.splitext(os.path.basename(name))[0]
        for name in python_sources
    }
    local_modules.update(
        name.split("/", 1)[0]
        for name in python_sources
        if "/" in name
    )
    imported_modules: set[str] = set()

    for filename, source in python_sources.items():
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as error:
            add_error(
                f"Синтаксична помилка {filename}: рядок {error.lineno}: {error.msg}."
            )
            continue

        # Resolve import aliases before inspecting calls. This catches aliased
        # os modules and functions imported directly from os or builtins.
        os_modules = {"os"}
        pickle_modules = {"pickle"}
        builtins_modules = {"builtins"}
        subprocess_modules = {"subprocess"}
        dangerous_function_aliases: set[str] = set()
        getenv_aliases: set[str] = set()
        environ_aliases: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    imported_modules.add(root)
                    bound_name = alias.asname or root
                    if root == "os":
                        os_modules.add(bound_name)
                    elif root == "pickle":
                        pickle_modules.add(bound_name)
                        add_error(f"У {filename} заборонено імпорт pickle.")
                    elif root == "subprocess":
                        subprocess_modules.add(bound_name)
                        add_error(f"У {filename} заборонено імпорт subprocess.")
                    elif root == "builtins":
                        builtins_modules.add(bound_name)

            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".", 1)[0]
                if module:
                    imported_modules.add(module)
                if module == "subprocess":
                    add_error(f"У {filename} заборонено імпорт subprocess.")
                elif module == "pickle":
                    add_error(f"У {filename} заборонено імпорт pickle.")
                elif module == "os":
                    for alias in node.names:
                        bound_name = alias.asname or alias.name
                        if alias.name in dangerous_os_calls:
                            dangerous_function_aliases.add(bound_name)
                        elif alias.name == "getenv":
                            getenv_aliases.add(bound_name)
                        elif alias.name == "environ":
                            environ_aliases.add(bound_name)
                elif module == "builtins":
                    for alias in node.names:
                        if alias.name in {"eval", "exec", "__import__"}:
                            dangerous_function_aliases.add(alias.asname or alias.name)

        def is_secret_argument(call_node: ast.Call) -> bool:
            return bool(
                call_node.args
                and isinstance(call_node.args[0], ast.Constant)
                and isinstance(call_node.args[0].value, str)
                and call_node.args[0].value in forbidden_secrets
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func

                if isinstance(function, ast.Name):
                    if function.id in {"eval", "exec", "__import__"}:
                        add_error(f"У {filename} заборонено виклик {function.id}().")
                    elif function.id in dangerous_function_aliases:
                        add_error(
                            f"У {filename} заборонено виклик небезпечної функції "
                            f"{function.id}()."
                        )
                    if function.id in getenv_aliases and is_secret_argument(node):
                        add_error(
                            f"У {filename} заборонено читати захищену змінну "
                            f"{node.args[0].value}."
                        )

                elif isinstance(function, ast.Attribute):
                    owner = function.value
                    if isinstance(owner, ast.Name):
                        if owner.id in os_modules and function.attr in dangerous_os_calls:
                            add_error(
                                f"У {filename} заборонено виклик os.{function.attr}()."
                            )
                        if owner.id in os_modules and function.attr == "getenv" and is_secret_argument(node):
                            add_error(
                                f"У {filename} заборонено читати захищену змінну "
                                f"{node.args[0].value}."
                            )
                        if owner.id in pickle_modules and function.attr in {"load", "loads"}:
                            add_error(
                                f"У {filename} заборонено десеріалізацію через pickle."
                            )
                        if owner.id in builtins_modules and function.attr in {"eval", "exec", "__import__"}:
                            add_error(
                                f"У {filename} заборонено виклик builtins.{function.attr}()."
                            )
                        if owner.id in environ_aliases and function.attr in {"get", "__getitem__"} and is_secret_argument(node):
                            add_error(
                                f"У {filename} заборонено читати захищену змінну "
                                f"{node.args[0].value}."
                            )
                        if owner.id in subprocess_modules:
                            add_error(
                                f"У {filename} заборонено виклики subprocess."
                            )

                    elif isinstance(owner, ast.Attribute):
                        if (
                            isinstance(owner.value, ast.Name)
                            and owner.value.id in os_modules
                            and owner.attr == "environ"
                            and function.attr in {"get", "__getitem__"}
                            and is_secret_argument(node)
                        ):
                            add_error(
                                f"У {filename} заборонено читати захищену змінну "
                                f"{node.args[0].value}."
                            )

            elif isinstance(node, ast.Subscript):
                owner = node.value
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in forbidden_secrets:
                        if (
                            isinstance(owner, ast.Attribute)
                            and isinstance(owner.value, ast.Name)
                            and owner.value.id in os_modules
                            and owner.attr == "environ"
                        ) or (
                            isinstance(owner, ast.Name)
                            and owner.id in environ_aliases
                        ):
                            add_error(
                                f"У {filename} заборонено читати захищену змінну {key.value}."
                            )

    for module in sorted(imported_modules):
        if module in standard_library or module in local_modules:
            continue
        expected_distributions = known_import_distributions.get(module)
        if expected_distributions and not (expected_distributions & requirement_names):
            expected = " або ".join(sorted(expected_distributions))
            add_error(
                f"У requirements.txt не вказана залежність для імпорту {module} "
                f"(додай одну з: {expected})."
            )

    if require_discord:
        requirements = files.get("requirements.txt", "")
        requirement_lines = [
            line.split("#", 1)[0].strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        has_discord_dependency = any(
            re.match(
                r"^(?:discord\.py|py-cord)(?:\s*(?:[<>=!~;\[]|$))",
                line,
                re.IGNORECASE,
            )
            for line in requirement_lines
        )
        if not has_discord_dependency:
            add_error(
                "У requirements.txt не вказана залежність discord.py або py-cord."
            )

    if not bot2 or not main_code:
        return errors

    checks = [
        (r"import\s+discord|from\s+discord", "Discord.py"),
        (r"on_message|wait_for\s*\(|message_content", "обробка повідомлень Discord"),
        (r"member_count|BOT2_MIN_GUILD_MEMBERS|MIN_GUILD_MEMBERS", "фільтр серверів за кількістю учасників"),
        (r"\b500\b|BOT2_MIN_GUILD_MEMBERS", "мінімум 500 учасників"),
        (r"moderator|модератор|approval|approve|погодж", "модерація та ручне підтвердження"),
        (r"sqlite|asyncpg|psycopg|DATABASE_URL|database", "постійне збереження даних"),
        (r"europe|european|eu\b|європ", "пріоритет європейських спільнот"),
        (r"website|web\s*site|discord\s*bot|application|software|сайт|застосунок|розроб", "пошук IT-замовлень"),
        (r"dedup|duplicate|дубл", "захист від дублікатів"),
        (r"/health|health_handler|discord_ready", "перевірка готовності Discord у health endpoint"),
    ]
    for pattern, label in checks:
        if not re.search(pattern, main_code, re.IGNORECASE):
            add_error(f"У main.py не знайдено реалізацію: {label}.")

    readme = files.get("README.md", "").casefold()
    if "discord" not in readme or not re.search(r"environment|змін", readme):
        add_error("README.md не містить достатніх інструкцій Discord та Environment Variables.")

    return errors


async def generate_software_project(client_task: str):
    bot2 = is_bot2_project(client_task)
    platform = "Discord" if bot2 else detect_bot_platform(client_task)
    special_requirements = bot2_project_spec() if bot2 else ""

    prompt = f"""
Ти — senior software developer системи Grox.

Створи ПОВНІСТЮ ГОТОВИЙ до запуску програмний проєкт
за технічним завданням клієнта.

ПЛАТФОРМА:
{platform}

ТЕХНІЧНЕ ЗАВДАННЯ:
{client_task}

{special_requirements}

ЗАГАЛЬНІ КРИТИЧНІ ВИМОГИ:
- Реалізуй саме функції з ТЗ; код має бути робочим, не макетом.
- Не залишай TODO, pass як заміну функціональності або фальшиві кнопки.
- Проєкт повинен запускатися командою `python main.py`.
- Створи health HTTP endpoint на `/`, який слухає PORT з environment variable та повертає JSON `{{"status":"ok","discord_ready":true}}` після підключення Discord та `false` до підключення; для Discord true дозволено лише після успішного підключення до Gateway.
- Для Discord використовуй discord.py та читай токен тільки з DISCORD_TOKEN.
- Не вставляй секрети в код, README, логи або GitHub.
- Усі токени/API keys бери з environment variables; не вигадуй їх.
- Створи requirements.txt з усіма залежностями та повний README.md.
- main.py має запускати health server паралельно з основним ботом.
- Якщо credentials відсутні, повідомляй про це зрозуміло й без витоку секретів.
- Якщо це Bot 2, суворо дотримуйся Discord-only вимог вище; не додавай інші платформи.

ПОВЕРНИ РІВНО ТРИ ФАЙЛИ В ТАКОМУ ФОРМАТІ:

===FILE: main.py===
<повний код>

===FILE: requirements.txt===
<повний вміст>

===FILE: README.md===
<повний вміст>

Не використовуй Markdown fences навколо файлів.
"""

    generation_result = await groq_request(
        prompt,
        max_output_tokens=30000,
        return_metadata=True,
    )
    result = generation_result["content"]
    generation_truncated = generation_result["finish_reason"] == "length"

    def extract_project_files(raw_response: str) -> dict[str, str]:
        """Extract named project files, tolerating common harmless LLM formatting variations."""
        extracted: dict[str, str] = {}
        marker = re.compile(
            r"(?im)^\s*(?:```[^\n]*\n\s*)?===\s*FILE\s*:\s*"
            r"(main\.py|requirements\.txt|README\.md)\s*===\s*$"
        )
        matches = list(marker.finditer(raw_response or ""))
        for index, match in enumerate(matches):
            filename = match.group(1)
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_response)
            body = raw_response[body_start:body_end].strip()
            body = re.sub(r"^\s*```(?:python|text|markdown)?\s*", "", body, flags=re.IGNORECASE)
            body = re.sub(r"\s*```\s*$", "", body)
            if body.strip():
                extracted[filename] = body.strip()

        # Some models omit the main.py marker or wrap the file in a Python
        # code fence. Recover only syntactically valid, recognisable entry-point
        # code; never treat arbitrary README snippets as the project main file.
        if not extracted.get("main.py"):
            candidates = []

            for fenced in re.finditer(
                r"```(?:python|py)[ \t]*\r?\n(.*?)(?:```|\Z)",
                raw_response or "",
                re.IGNORECASE | re.DOTALL,
            ):
                candidates.append(fenced.group(1).strip())

            if not matches:
                candidates.append((raw_response or "").strip())
            elif matches:
                # If the model placed an unmarked main.py before the first
                # marked file, inspect only that unmarked prefix.
                prefix = (raw_response or "")[:matches[0].start()].strip()
                if prefix:
                    candidates.append(prefix)

            for candidate in candidates:
                candidate = re.sub(
                    r"^\s*```(?:python|py)?[ \t]*\r?\n?",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                )
                candidate = re.sub(r"\s*```\s*$", "", candidate).strip()

                # Ignore any explanatory preamble before the first Python
                # import, while preserving the complete remaining source.
                import_match = re.search(
                    r"(?m)^(?:import\s+[A-Za-z_]|from\s+[A-Za-z_])",
                    candidate,
                )
                if import_match:
                    candidate = candidate[import_match.start():].strip()

                try:
                    tree = ast.parse(candidate, filename="generated_main.py")
                except SyntaxError:
                    continue

                has_import = any(
                    isinstance(node, (ast.Import, ast.ImportFrom))
                    for node in ast.walk(tree)
                )
                has_entrypoint = any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "main"
                    for node in ast.walk(tree)
                ) or any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"run", "start"}
                    for node in ast.walk(tree)
                ) or any(
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and "__name__" in ast.unparse(node.test)
                    and "__main__" in ast.unparse(node.test)
                    for node in ast.walk(tree)
                )

                if has_import and has_entrypoint:
                    extracted["main.py"] = candidate
                    break

        return extracted

    files = extract_project_files(result)
    print(
        "[PROJECT GENERATION] "
        f"response_chars={len(result)} "
        f"finish_reason={generation_result.get('finish_reason')} "
        f"usage={generation_result.get('usage', {})} "
        f"extracted={sorted(files)} "
        f"missing={[name for name in ('main.py', 'requirements.txt', 'README.md') if not files.get(name, '').strip()]}"
    )
    validation_errors = validate_generated_project(
        files, bot2=bot2, require_discord=(platform == "Discord")
    )
    if generation_truncated:
        validation_errors.append(
            "Groq обрізав первинну відповідь за лімітом токенів; потрібне повторне повне генерування."
        )
    # Run a bounded repair/validation cycle. Never publish files unless the
    # deterministic validator reports zero errors after a complete repair.
    max_repair_rounds = 3
    repair_round = 0

    while validation_errors and repair_round < max_repair_rounds:
        repair_round += 1

        missing_files = [
            name for name in ("main.py", "requirements.txt", "README.md")
            if not files.get(name, "").strip()
        ]
        repair_targets = list(missing_files)

        error_text = "\n".join(validation_errors)
        if "main.py" in error_text and "main.py" not in repair_targets:
            repair_targets.insert(0, "main.py")
        if "requirements.txt" in error_text and "requirements.txt" not in repair_targets:
            repair_targets.append("requirements.txt")
        if "README.md" in error_text and "README.md" not in repair_targets:
            repair_targets.append("README.md")

        # If the first answer was truncated, repair the last file that began.
        if repair_round == 1 and generation_truncated:
            file_marker = re.compile(
                r"(?im)^\s*===\s*FILE\s*:\s*"
                r"(main\.py|requirements\.txt|README\.md)\s*===\s*$"
            )
            generation_markers = list(file_marker.finditer(result or ""))
            truncated_target = (
                generation_markers[-1].group(1)
                if generation_markers else "main.py"
            )
            if truncated_target not in repair_targets:
                repair_targets.append(truncated_target)

        if not repair_targets:
            repair_targets = ["main.py"]

        requested_files = "\n".join(
            f"===FILE: {name}===\n<повний виправлений вміст {name}>"
            for name in repair_targets
        )
        current_files_context = "\n\n".join(
            f"=== CURRENT FILE: {name} ===\n"
            + redact_sensitive_text(files.get(name, ""))
            for name in repair_targets
            if files.get(name, "").strip()
        )
        repair_prompt = (
            "Ти — senior Python/software engineer системи Grox. "
            "Це раунд виправлення " + str(repair_round) + " з "
            + str(max_repair_rounds) + ". Виправ усі наведені помилки. "
            "Цільові файли, які ОБОВ'ЯЗКОВО потрібно повернути повністю: "
            + ", ".join(repair_targets)
            + ". Прочитай поточний вміст кожного наявного файлу та збережи "
            "його робочу функціональність. Відсутні файли створи повністю. "
            "Перевір синтаксис, лапки, дужки, двокрапки, відступи, імпорти "
            "та узгодженість між файлами. Не видаляй функції заради проходження "
            "перевірки. Поверни кожен цільовий файл повністю, без diff, "
            "фрагментів, пояснень або Markdown fences.\n\n"
            "ОБОВ'ЯЗКОВИЙ ФОРМАТ:\n" + requested_files
            + "\n\nПОТОЧНІ ФАЙЛИ:\n" + current_files_context
            + "\n\nПОМИЛКИ ПЕРЕВІРКИ:\n- "
            + "\n- ".join(validation_errors)
            + "\n\nПОЧАТКОВЕ ТЕХНІЧНЕ ЗАВДАННЯ:\n" + client_task
            + "\n\nПЛАТФОРМА:\n" + platform
            + "\n\nСПЕЦІАЛЬНІ ВИМОГИ:\n" + special_requirements
        )

        try:
            repair_generation = await groq_request(
                repair_prompt,
                max_output_tokens=30000,
                return_metadata=True,
            )
        except Exception as repair_error:
            print(
                "[PROJECT REPAIR] "
                f"round={repair_round}/{max_repair_rounds} "
                f"request_failed={type(repair_error).__name__}: {repair_error}"
            )
            validation_errors.append(
                f"Раунд виправлення {repair_round}: запит до Groq не виконано "
                f"({type(repair_error).__name__})."
            )
            continue

        repaired_result = repair_generation["content"]
        repair_truncated = repair_generation["finish_reason"] == "length"
        repaired_files = extract_project_files(repaired_result)
        returned_targets = [
            name for name in repair_targets
            if repaired_files.get(name, "").strip()
        ]
        missing_repair_targets = [
            name for name in repair_targets
            if name not in returned_targets
        ]
        print(
            "[PROJECT REPAIR] "
            f"round={repair_round}/{max_repair_rounds} "
            f"response_chars={len(repaired_result)} "
            f"extracted={sorted(repaired_files)} "
            f"requested_targets={repair_targets} "
            f"missing_targets={missing_repair_targets} "
            f"truncated={repair_truncated}"
        )

        # Do not merge partial or truncated responses. Keeping the previous
        # files is safe, but they must not be treated as repaired.
        if repair_truncated or missing_repair_targets:
            problems = []
            if repair_truncated:
                problems.append("Groq обрізав відповідь за лімітом токенів")
            if missing_repair_targets:
                problems.append(
                    "Groq не повернув повні цільові файли: "
                    + ", ".join(missing_repair_targets)
                )
            validation_errors.extend(
                f"Раунд виправлення {repair_round}: " + problem + "."
                for problem in problems
            )
            continue

        files.update({
            name: repaired_files[name]
            for name in repair_targets
        })
        validation_errors = validate_generated_project(
            files, bot2=bot2, require_discord=(platform == "Discord")
        )

    if validation_errors:
        validation_errors.insert(
            0,
            f"Вичерпано {max_repair_rounds} раунди виправлення; "
            "проєкт не пройшов фінальну перевірку.",
        )
    if validation_errors:
        raise RuntimeError(
            "Згенерований проєкт не пройшов перевірку навіть після повторного виправлення; "
            "файли НЕ опубліковано:\n- " + "\n- ".join(validation_errors)
        )

    return files


async def wait_for_bot2_ready(service_url: str, thread: discord.Thread) -> bool:
    """Wait for the deployed Bot 2 to report that its Discord client is ready."""
    if not service_url:
        await thread.send(
            "⚠️ Render створив сервіс, але не повернув URL. "
            "Не можу підтвердити готовність Bot 2. Перевір сервіс у Render."
        )
        return False

    health_url = service_url.rstrip("/") + "/health"
    deadline = asyncio.get_running_loop().time() + BOT2_DEPLOY_WAIT_SECONDS
    last_problem = "Render ще запускає сервіс."

    await thread.send(
        "⏳ **Очікую запуск Bot 2 у Render та перевіряю підключення до Discord...**"
    )

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.get(health_url) as response:
                    body = await response.text()
                    if response.status == 200:
                        try:
                            payload = json.loads(body)
                        except json.JSONDecodeError:
                            payload = {}
                        if (
                            isinstance(payload, dict)
                            and payload.get("status") == "ok"
                            and payload.get("discord_ready") is True
                        ):
                            await thread.send(
                                "✅ **Bot 2 запущений!** Render health-check підтвердив, "
                                "що бот підключений і готовий у Discord."
                            )
                            return True
                        last_problem = (
                            "Health endpoint відповідає, але Discord ще не підтвердив готовність."
                        )
                    else:
                        last_problem = f"Health endpoint повернув HTTP {response.status}."
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_problem = f"Очікування Render: {type(error).__name__}."

            await asyncio.sleep(max(3, BOT2_HEALTH_POLL_INTERVAL))

    await thread.send(
        "❌ **Не вдалося підтвердити запуск Bot 2.**\n\n"
        f"Останній стан: {last_problem}\n"
        "Код збережений у GitHub, але я НЕ позначаю Bot 2 як готовий. "
        "Перевір Build/Deploy Logs у Render."
    )
    return False



async def wait_for_generated_service_ready(
    service_url: str,
    thread: discord.Thread,
    require_discord_ready: bool = False,
) -> bool:
    """Do not report a generated service as complete until its health endpoint responds."""
    if not service_url:
        await thread.send("⚠️ Render створив сервіс, але не повернув URL; готовність не підтверджена.")
        return False

    health_url = service_url.rstrip("/") + "/"
    deadline = asyncio.get_running_loop().time() + BOT2_DEPLOY_WAIT_SECONDS
    last_problem = "Render ще запускає сервіс."
    timeout = aiohttp.ClientTimeout(total=25)

    await thread.send("⏳ **Перевіряю запуск створеного сервісу в Render...**")
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.get(health_url) as response:
                    body = await response.text()
                    if response.status == 200:
                        try:
                            payload = json.loads(body)
                        except json.JSONDecodeError:
                            payload = {}
                        healthy = isinstance(payload, dict) and payload.get("status") == "ok"
                        discord_ok = (not require_discord_ready) or (isinstance(payload, dict) and payload.get("discord_ready") is True)
                        if healthy and discord_ok:
                            await thread.send("✅ **Render health-check успішний. Сервіс відповідає та готовий.**")
                            return True
                        last_problem = "Health endpoint відповідає, але сервіс або Discord ще не готовий."
                    else:
                        last_problem = f"Health endpoint повернув HTTP {response.status}."
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_problem = f"Очікування Render: {type(error).__name__}."
            await asyncio.sleep(max(3, BOT2_HEALTH_POLL_INTERVAL))

    await thread.send(
        "❌ **Не вдалося підтвердити готовність сервісу.**\\n\\n"
        f"Останній стан: {last_problem}\\n"
        "Проєкт збережено в GitHub, але я не позначаю його як готовий. Перевір Render Logs."
    )
    return False



# ============================================================
# GITHUB CONTENTS API
# ============================================================

GITHUB_API_URL = "https://api.github.com"
GITHUB_WRITE_LOCK = asyncio.Lock()

_GITHUB_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def validate_github_repo(value) -> str:
    """GITHUB_REPO має бути рівно `owner/repository` (без URL, .git і пробілів)."""
    repo = value or ""

    if (
        not _GITHUB_REPO_RE.fullmatch(repo)
        or repo.lower().endswith(".git")
    ):
        raise RuntimeError(
            "GITHUB_REPO має формат `owner/repository`, наприклад "
            "`myname/grox-projects` (без https://github.com/, без .git, "
            "без пробілів і переносів рядка). "
            "Перевір GITHUB_REPO у Render Environment; значення не виводиться з міркувань безпеки."
        )

    return repo


async def _diagnose_github_404(session, headers, repo, branch) -> str:
    """
    Після PUT 404 з'ясовує, ЩО саме GitHub не знайшов:
    репозиторій / доступ токена, право запису чи гілку.
    Токен ніколи не потрапляє у відповідь.
    """
    async with session.get(
        f"{GITHUB_API_URL}/repos/{repo}",
        headers=headers
    ) as response:
        status = response.status
        repo_data = await response.json() if status == 200 else {}

    if status == 404:
        return (
            f"Репозиторій `{repo}` не знайдено ЦИМ токеном. GitHub повертає "
            "404 і тоді, коли репозиторій приватний, а токен не має до нього "
            "доступу. Перевір owner/назву в GITHUB_REPO і права токена: "
            "fine-grained — вибрати цей репозиторій та Contents: "
            "Read and write; classic — scope `repo`."
        )

    if status in (401, 403):
        return (
            f"GitHub відхилив токен під час перевірки репозиторію "
            f"(HTTP {status}). Токен недійсний або без потрібних прав."
        )

    if status != 200:
        return f"Перевірка репозиторію повернула HTTP {status}."

    permissions = repo_data.get("permissions") or {}

    if permissions and not permissions.get("push"):
        return (
            f"Токен бачить `{repo}`, але не має права запису (push). "
            "Потрібно Contents: Read and write (fine-grained) "
            "або scope `repo` (classic)."
        )

    async with session.get(
        f"{GITHUB_API_URL}/repos/{repo}/branches/"
        f"{quote(branch, safe='/')}",
        headers=headers
    ) as response:
        branch_status = response.status

    if branch_status == 404:
        return (
            f"Репозиторій `{repo}` доступний, але гілки `{branch}` у ньому "
            "немає. Створи її або змінь GITHUB_BRANCH "
            "(наприклад, на `master`)."
        )

    return (
        f"Репозиторій `{repo}` і гілка `{branch}` доступні, тому причину "
        "404 за цими перевірками встановити не вдалося."
    )


async def _diagnose_github_403(session, headers, repo) -> str:
    """Перевіряє, ким GitHub бачить PAT і чи має він push-доступ до repo."""
    details = []

    async with session.get(
        f"{GITHUB_API_URL}/user", headers=headers
    ) as response:
        user_status = response.status
        user_data = await response.json() if user_status == 200 else {}

    async with session.get(
        f"{GITHUB_API_URL}/repos/{repo}", headers=headers
    ) as response:
        repo_status = response.status
        repo_data = await response.json() if repo_status == 200 else {}

    if user_status == 200:
        login = user_data.get("login")
        if login:
            details.append(f"PAT авторизований як @{login}")
            owner = (repo_data.get("owner") or {}).get("login")
            if owner and login.lower() != owner.lower():
                details.append(
                    f"власник репозиторію — @{owner}, а власник PAT — @{login}"
                )
    else:
        details.append(f"перевірка власника PAT повернула HTTP {user_status}")

    if repo_status == 200:
        permissions = repo_data.get("permissions") or {}
        if "push" in permissions:
            details.append(
                "GitHub підтверджує push-доступ до репозиторію"
                if permissions.get("push")
                else "GitHub НЕ підтверджує push-доступ до репозиторію"
            )
        else:
            details.append("GitHub не повернув permissions.push для цього PAT")
    else:
        details.append(
            f"перевірка доступу до репозиторію повернула HTTP {repo_status}"
        )

    return "Діагностика PAT: " + "; ".join(details) + "."


def _safe_github_error_detail(status: int, body: str, request_id: str | None = None) -> str:
    """Повертає корисну, але безпечну діагностику відповіді GitHub API."""
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}

    raw_message = payload.get("message", "") if isinstance(payload, dict) else ""
    if not isinstance(raw_message, str):
        raw_message = ""

    # Не показуємо токен або заголовок авторизації навіть якщо вони випадково є у відповіді.
    detail = raw_message.replace(GITHUB_TOKEN, "[REDACTED]") if GITHUB_TOKEN else raw_message
    detail = re.sub(r"(?i)(Bearer|token)\s+[A-Za-z0-9_\-]+", r"\1 [REDACTED]", detail)
    detail = re.sub(r"[\r\n\t]+", " ", detail)
    detail = re.sub(r"\s{2,}", " ", detail).strip()
    detail = detail[:500] or "GitHub не надав текстового пояснення."

    documentation_url = payload.get("documentation_url", "") if isinstance(payload, dict) else ""
    if not isinstance(documentation_url, str) or not documentation_url.startswith("https://docs.github.com/"):
        documentation_url = ""

    safe_request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9-]{1,100}", request_id) else ""
    parts = [f"GitHub API: {detail}"]
    if documentation_url:
        parts.append(f"Документація: {documentation_url}")
    if safe_request_id:
        parts.append(f"Request ID: {safe_request_id}")
    return f"HTTP {status}. " + " | ".join(parts)


async def github_upsert_file(
    repo_path: str,
    file_path: str,
    content: str,
    commit_message: str
):
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN не налаштований.")

    if not GITHUB_REPO:
        raise RuntimeError("GITHUB_REPO не налаштований.")

    repo = validate_github_repo(GITHUB_REPO)

    encoded_path = "/".join(
        quote(part, safe="")
        for part in file_path.split("/")
    )

    url = (
        f"{GITHUB_API_URL}/repos/"
        f"{repo}/contents/{encoded_path}"
    )

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    branch = GITHUB_BRANCH

    timeout = aiohttp.ClientTimeout(total=60)

    async with GITHUB_WRITE_LOCK:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            sha = None
    
            async with session.get(
                url,
                headers=headers,
                params={"ref": branch}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    sha = data.get("sha")
                elif response.status != 404:
                    body = await response.text()
                    request_id = response.headers.get("X-GitHub-Request-Id")
                    detail = _safe_github_error_detail(response.status, body, request_id)
                    raise RuntimeError(f"GitHub GET failed: {detail}")
    
            payload = {
                "message": commit_message,
                "content": base64.b64encode(
                    content.encode("utf-8")
                ).decode("ascii"),
                "branch": branch,
            }
    
            if sha:
                payload["sha"] = sha
    
            async with session.put(
                url,
                headers=headers,
                json=payload
            ) as response:
                status = response.status
                body = await response.text()
                request_id = response.headers.get("X-GitHub-Request-Id")
                accepted_permissions = response.headers.get("X-Accepted-GitHub-Permissions", "")
    
            if status not in (200, 201):
                detail = _safe_github_error_detail(status, body, request_id)
                if accepted_permissions:
                    safe_permissions = re.sub(r"[^A-Za-z0-9_=,; -]", "", accepted_permissions)[:200]
                    detail += f" | Потрібні права GitHub API (X-Accepted-GitHub-Permissions): {safe_permissions}"
                message = f"GitHub PUT failed: {detail}"
    
                if status == 404:
                    # Для PUT 404 додатково перевіряємо доступ до репозиторію та гілки.
                    try:
                        diagnosis = await _diagnose_github_404(
                            session, headers, repo, branch
                        )
                    except Exception as diag_error:
                        diagnosis = (
                            "Діагностика не вдалася: "
                            f"{type(diag_error).__name__}"
                        )
                    message += f"\\nДіагностика: {diagnosis}"
                elif status == 403:
                    # Перевіряємо реального власника PAT і push-доступ.
                    try:
                        diagnosis = await _diagnose_github_403(
                            session, headers, repo
                        )
                    except Exception as diag_error:
                        diagnosis = (
                            "Діагностика PAT не вдалася: "
                            f"{type(diag_error).__name__}"
                        )
                    message += f"\\n{diagnosis}"
    
                raise RuntimeError(message)
    
            data = json.loads(body)

    return data


# ============================================================
# RENDER DEPLOY
# ============================================================

async def deploy_project_to_render(
    service_name: str,
    root_dir: str,
    extra_env: dict[str, str] | None = None,
    is_bot2_service: bool = False
):
    if not RENDER_API_KEY:
        raise RuntimeError(
            "RENDER_API_KEY не налаштований."
        )

    if not GITHUB_REPO:
        raise RuntimeError(
            "GITHUB_REPO не налаштований."
        )

    repo = validate_github_repo(GITHUB_REPO)

    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    api_url = "https://api.render.com/v1"

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        # Render requires ownerId when creating a service.
        async with session.get(
            f"{api_url}/owners",
            headers=headers
        ) as response:
            owners_body = await response.text()

            if response.status != 200:
                raise RuntimeError(
                    f"Render owners request failed HTTP {response.status}; response body omitted."
                )

            try:
                owners_data = json.loads(owners_body)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Render owners повернув некоректний JSON; відповідь API приховано."
                ) from error

        # Render API versions/proxies may return either a list directly or
        # an object containing an owners list. Handle both shapes explicitly.
        if isinstance(owners_data, list):
            owners = owners_data
        elif isinstance(owners_data, dict):
            owners = owners_data.get("owners") or owners_data.get("items") or []
        else:
            owners = []

        if not owners or not isinstance(owners[0], dict):
            raise RuntimeError(
                "Render API не повернув коректний список owner; відповідь API приховано."
            )

        first_owner = owners[0]
        nested_owner = first_owner.get("owner")
        if isinstance(nested_owner, dict):
            owner_id = nested_owner.get("id") or first_owner.get("id")
        else:
            owner_id = first_owner.get("id")

        if not owner_id:
            raise RuntimeError(
                "Не вдалося визначити ownerId Render."
            )

        repo_url = (
            f"https://github.com/{repo}.git"
        )

        env_vars = [
            {
                "key": "PORT",
                "value": "10000",
            }
        ]

        # Generated services receive only credentials explicitly required
        # by their task. Never copy Grox's unrelated platform credentials.
        optional_env = dict(extra_env or {})

        for key, value in optional_env.items():
            if value:
                env_vars.append(
                    {
                        "key": key,
                        "value": str(value),
                    }
                )

        # Per the Render API: envVars is a TOP-LEVEL field, and
        # buildCommand / startCommand belong in
        # serviceDetails.envSpecificDetails (not directly in serviceDetails).
        payload = {
            "type": "web_service",
            "name": service_name,
            "ownerId": owner_id,
            "repo": repo_url,
            "branch": GITHUB_BRANCH,
            "rootDir": root_dir,
            "envVars": env_vars,
            "serviceDetails": {
                "runtime": "python",
                "plan": "free",
                "region": "frankfurt",
                "healthCheckPath": (
                    "/health" if is_bot2_service else "/"
                ),
                "envSpecificDetails": {
                    "buildCommand": (
                        "pip install -r requirements.txt"
                    ),
                    "startCommand": "python main.py",
                },
            },
        }

        async with session.post(
            f"{api_url}/services",
            headers=headers,
            json=payload
        ) as response:
            body = await response.text()

            if response.status not in (200, 201):
                raise RuntimeError(
                    f"Render service creation failed HTTP {response.status}; відповідь API приховано."
                )

            try:
                data = json.loads(body)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Render service creation повернув некоректний JSON; відповідь API приховано."
                ) from error

    # Render may return a service object, a wrapped object, or a one-item list.
    if isinstance(data, list):
        service = data[0] if data and isinstance(data[0], dict) else {}
    elif isinstance(data, dict):
        service = data.get("service", data)
        if isinstance(service, list):
            service = service[0] if service and isinstance(service[0], dict) else {}
    else:
        service = {}

    if not isinstance(service, dict):
        raise RuntimeError(
            "Render API повернув неочікувану відповідь сервісу; вміст відповіді приховано."
        )

    service_details = service.get("serviceDetails")
    if not isinstance(service_details, dict):
        service_details = {}

    # Render's service-list response nests the primary URL under
    # serviceDetails.url; creation responses may expose it at the top level.
    service_url = service.get("url") or service_details.get("url")

    return {
        "id": service.get("id"),
        "url": service_url,
        "name": service.get("name", service_name),
    }


async def build_and_deploy_software_bot(
    order: Order,
    thread: discord.Thread
):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        await thread.send(
            "⚠️ **Не можу опублікувати проєкт у GitHub.**\n\n"
            "Потрібні GITHUB_TOKEN та GITHUB_REPO у Render Environment."
        )
        return False

    # Validate once before generation or publishing. Never echo the raw
    # environment value in Discord messages.
    repo = validate_github_repo(GITHUB_REPO)

    if not RENDER_API_KEY:
        await thread.send(
            "⚠️ **Не можу створити сервіс у Render.**\n\n"
            "Потрібен RENDER_API_KEY у Render Environment."
        )
        return False

    if order.client_id != GROX_OWNER_ID:
        await thread.send(
            "🔒 Розгортання дозволене лише власнику Grox. "
            "GitHub і Render не змінювалися."
        )
        return False

    bot2 = is_bot2_project(order.task)
    generated_platform = "Discord" if bot2 else detect_bot_platform(order.task)

    if generated_platform != "Discord":
        await thread.send(
            "⚠️ **Зараз Grox автоматично створює лише Discord-ботів.**\n\n"
            "Інші платформи не розгортатиму, щоб не передавати зайві облікові дані."
        )
        return False

    if GENERATED_DISCORD_TOKEN and GENERATED_DISCORD_TOKEN == DISCORD_TOKEN:
        await thread.send(
            "🔒 **Токен нового бота збігається з токеном самого Grox.**\n\n"
            "Створи окремий Discord application/bot і налаштуй його токен "
            "у GROX_BUILDER_DISCORD_TOKEN. Не використовуй токен Grox."
        )
        return False

    if generated_platform == "Discord" and not GENERATED_DISCORD_TOKEN:
        await thread.send(
            "⚠️ **Не можу запустити Discord-бота без його токена.**\n\n"
            "Додай токен створеного Discord-бота в Render Environment "
            "Grox під назвою `GROX_BUILDER_DISCORD_TOKEN`.\n"
            "Не надсилай токен у чат і не додавай його в GitHub."
        )
        return False

    if generated_platform == "Discord" and builder_token_services:
        await thread.send(
            "⚠️ **Увага: токен `GROX_BUILDER_DISCORD_TOKEN` уже використовує "
            "сервіс: " + ", ".join(builder_token_services[-3:]) + ".**\n\n"
            "Два сервіси з одним токеном одночасно відповідатимуть двічі. "
            "Для кожного окремого Discord-бота потрібен власний токен."
        )

    await thread.send(
        "🤖 **Генерую програмний проєкт...**\n\n"
        + ("Для Bot 2 застосовую окреме Discord-only технічне завдання та перевірки."
           if bot2 else "Створюю код, залежності та інструкції.")
    )

    # generate_software_project() already validates the files and raises
    # RuntimeError (with the list of problems) if validation fails, so
    # nothing is published to GitHub in that case.
    files = await generate_software_project(order.task)

    # Unique suffix: order numbers restart after every Grox restart, and
    # Render service names must be unique within the workspace.
    suffix = uuid.uuid4().hex[:6]
    root_dir = f"generated_projects/order_{order.order_id}_{suffix}"
    service_name = f"grox-order-{order.order_id}-{suffix}"

    await thread.send("📦 **Публікую перевірені файли в GitHub...**")

    for filename, content in files.items():
        await github_upsert_file(
            root_dir,
            f"{root_dir}/{filename}",
            content,
            f"Grox: generate order #{order.order_id} - {filename}"
        )

    order.project_repo_path = root_dir
    await save_order(order)
    await thread.send("☁️ **Створюю окремий Render-сервіс...**")

    render_env: dict[str, str] = {}

    # Generated projects may use Groq independently of Grox. Pass the
    # dedicated Groq key only when the generated source explicitly references
    # GROQ_API_KEY; never copy Render/GitHub/Vercel or Grox database secrets.
    if any("GROQ_API_KEY" in content for content in files.values()):
        render_env["GROQ_API_KEY"] = GROQ_API_KEY

    if generated_platform == "Discord":
        render_env["DISCORD_TOKEN"] = GENERATED_DISCORD_TOKEN
    if bot2:
        render_env.update({
            "BOT2_MODE": "1",
            "BOT2_MIN_GUILD_MEMBERS": str(BOT2_MIN_GUILD_MEMBERS),
        })
        if BOT2_DATABASE_URL:
            render_env["DATABASE_URL"] = BOT2_DATABASE_URL

    render_info = await deploy_project_to_render(
        service_name,
        root_dir,
        extra_env=render_env,
        is_bot2_service=bot2
    )

    order.render_service_id = render_info.get("id")
    order.deployment_url = render_info.get("url")
    await save_order(order)

    if generated_platform == "Discord":
        builder_token_services.append(
            render_info.get("name") or service_name
        )

    if bot2:
        # A successful Render API response only means that the service was
        # created. It does NOT prove the Discord bot is connected.
        ready = await wait_for_bot2_ready(order.deployment_url, thread)
        if not ready:
            return False
        await thread.send(
            "📁 GitHub: `" + repo + "/" + root_dir + "`\n"
            + (f"🌐 Render: {order.deployment_url}" if order.deployment_url else "")
        )
        return True

    # A successful Render API response means only that a service record was created.
    ready = await wait_for_generated_service_ready(
        order.deployment_url,
        thread,
        require_discord_ready=(generated_platform == "Discord"),
    )
    if not ready:
        return False

    message = (
        "📦 **Проєкт опубліковано в GitHub і запущено в Render.**\n\n"
        f"📁 GitHub: `{repo}/{root_dir}`\n"
    )
    if order.deployment_url:
        message += f"🌐 Render: {order.deployment_url}\n"
    message += (
        "\n⚠️ Відповідь API підтверджує створення сервісу, "
        "але не є повним тестом функціональності проєкту."
    )
    await thread.send(message)
    return True


# ============================================================
# PROCESS ONE ORDER
# ============================================================

async def process_order(
    message: discord.Message
):
    if message.author.id != GROX_OWNER_ID:
        return

    if message.id in active_orders:
        return

    active_orders.add(
        message.id
    )

    active_clients[message.author.id] = (
        active_clients.get(message.author.id, 0) + 1
    )

    try:
        # ====================================================
        # CREATE ORDER
        # ====================================================

        order = await create_order(message)
        if order is None:
            # Another Render instance may already have processed this exact
            # Discord message. The database uniqueness constraint is the
            # cross-instance source of truth; do not create another thread.
            return

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
            await save_order(order)
            return

        except Exception as error:
            print(
                f"[THREAD ERROR] {safe_exception(error)}; details omitted."
            )

            print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")

            await message.channel.send(
                f"{message.author.mention}, "
                f"виникла технічна помилка."
            )

            order.status = "ERROR"
            await save_order(order)
            return

        order.thread_id = thread.id
        await save_order(order)

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
            f"💡 Вам не потрібно самостійно оцінювати завдання.\n\n"
            f"📋 Надішліть детальне ТЗ.\n\n"
            f"🧠 Grox уточнить вимоги, визначить тип проєкту та підготує результат."
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
            await save_order(order)
            return

        first_task = initial_message.content.strip()

        if not first_task:
            await thread.send(
                "❌ ТЗ не може бути порожнім."
            )
            order.status = "ERROR"
            await save_order(order)
            return

        if len(first_task) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ ТЗ занадто велике.\n"
                f"Максимум: {MAX_TASK_LENGTH} символів."
            )
            order.status = "ERROR"
            await save_order(order)
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
            await save_order(order)
            return

        if len(final_task) > MAX_TASK_LENGTH:
            await thread.send(
                "❌ **Фінальне ТЗ перевищує допустимий розмір.**\\n\\n"
                f"Максимум — {MAX_TASK_LENGTH} символів. Я не буду мовчки "
                "обрізати вимоги, щоб не втратити важливу частину завдання. "
                "Скоротіть ТЗ і створіть нове замовлення."
            )
            order.status = "ERROR"
            await save_order(order)
            return

        order.task = final_task
        await save_order(order)

        await thread.send(
            "📋 **Фінальне ТЗ сформовано.**\n\n"
            "🧠 Тепер перевірю тип проєкту та підготую його виконання."
        )

        # ====================================================
        # CLIENT BUDGET
        # ====================================================

        order.client_budget = extract_budget(
            order.task
        )

        # ====================================================
        # START WORK — PRICE ESTIMATION DISABLED
        # ====================================================

        order.project_type = "IT-проєкт"
        order.complexity = "Не оцінюється"
        order.price_reason = ""
        order.budget = 0
        order.client_approved_price = True
        order.status = "IN_PROGRESS"
        await save_order(order)

        await thread.send(
            "✅ **Технічне завдання прийнято!**\n\n"
            "💰 Grox не оцінює вартість і не запитує погодження ціни.\n"
            "🤖 Починаю виконання проєкту..."
        )

        # ====================================================
        # PROJECT TYPE DETECTION
        # ====================================================

        website_project = (
            not is_software_bot_project(order.task)
            and has_keyword(
                order.task,
                (
                    "сайт",
                    "website",
                    "web",
                    "лендинг",
                    "landing",
                    "вебсайт",
                )
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
                    f"Safe diagnostic: {safe_error_detail(error)}"
                )

                print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")

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
                await save_order(order)
                return

        # ====================================================
        # SOFTWARE BOT
        # ====================================================

        elif is_software_bot_project(order.task):
            try:
                success = await build_and_deploy_software_bot(
                    order,
                    thread
                )

            except Exception as error:
                print(
                    "========================================"
                )
                print(
                    "[SOFTWARE BOT PROCESS ERROR]"
                )
                print(
                    f"Type: {type(error).__name__}"
                )
                safe_error = safe_error_detail(error)
                print(f"Safe diagnostic: {safe_error}")
                print("[TRACEBACK OMITTED] Секрети та повний traceback не виводяться.")
                print(
                    "========================================"
                )

                await thread.send(
                    "❌ **Не вдалося завершити створення "
                    "програмного бота.**\n\n"
                    "🔒 Фінальна передача заблокована.\n\n"
                    f"**Технічна причина:** `{safe_error}`"
                )

                order.status = "ERROR"
                return

            if not success:
                order.status = "ERROR"
                return

        # ====================================================
        # OTHER PROJECT — NOT SUPPORTED AUTOMATICALLY
        # ====================================================

        else:
            # Previously Grox said "Проєкт виконано" here although nothing
            # was built. It must not claim a delivery that did not happen.
            await thread.send(
                "ℹ️ **Цей тип проєкту Grox поки не створює автоматично.**\n\n"
                "Зараз я можу автоматично зробити лише сайт або "
                "програмного бота. Ваше ТЗ збережено, замовлення "
                "не позначено як виконане."
            )

            order.status = "UNSUPPORTED"
            await save_order(order)
            return

        # ====================================================
        # PROJECT READY — PAYMENT TEMPORARILY DISABLED
        # ====================================================

        order.status = "COMPLETED"
        await save_order(order)

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
        await save_order(order)

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
        # Do not leave a failed order marked WAITING_TZ or IN_PROGRESS.
        # Completed/expired/unsupported orders keep their terminal status.
        if isinstance(locals().get("order"), Order) and order.status not in {
            "COMPLETED", "ERROR", "EXPIRED", "QA_FAILED",
            "UNSUPPORTED", "INTERRUPTED",
        }:
            order.status = "ERROR"

        print(
            "========================================"
        )

        print(
            "🔥 ORDER ERROR"
        )

        print(
            f"Type: {type(error).__name__}"
        )

        safe_error = safe_error_detail(error)
        print(f"Safe diagnostic: {safe_error}")
        print("[TRACEBACK OMITTED] Секрети та повний traceback не виводяться.")

        print(
            "========================================"
        )

        try:
            await message.channel.send(
                f"{message.author.mention}, "
                f"під час обробки замовлення сталася технічна помилка.\n"
                f"Безпечна причина: `{safe_error}`"
            )

        except Exception:
            pass

    finally:
        if isinstance(locals().get("order"), Order):
            try:
                await save_order(order)
            except Exception as persist_error:
                print(
                    f"[DATABASE ERROR] Could not persist order "
                    f"#{order.order_id}: {type(persist_error).__name__}; details omitted."
                )

        active_orders.discard(
            message.id
        )

        remaining = active_clients.get(message.author.id, 1) - 1

        if remaining > 0:
            active_clients[message.author.id] = remaining
        else:
            active_clients.pop(message.author.id, None)


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():
    global discord_ready
    discord_ready = True
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
        f"🧠 Groq model: {GROQ_MODEL}"
    )

    print(
        f"📡 Client channel: "
        f"{CLIENT_CHANNEL_ID}"
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
        f"🤖 Software bot generation: "
        f"{'YES' if GITHUB_TOKEN and GITHUB_REPO and RENDER_API_KEY else 'NO'}"
    )

    print(
        "🚀 Grox готовий!"
    )

    print(
        "========================================"
    )


@bot.event
async def on_disconnect():
    global discord_ready
    discord_ready = False
    print("[DISCORD] Gateway disconnected; waiting for reconnect...")


@bot.event
async def on_resumed():
    global discord_ready
    discord_ready = True
    print("[DISCORD] Gateway session resumed.")


# ============================================================
# MESSAGE HANDLER
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):
    if message.author.bot:
        return

    # Grox is a private assistant for its owner only. Ignore everyone else
    # before command processing or order detection, so outsiders cannot use
    # Groq, Vercel, GitHub, or Render resources.
    if message.author.id != GROX_OWNER_ID:
        return

    # Always process commands first.
    await bot.process_commands(
        message
    )

    # Only detect NEW orders in the configured client channel.
    #
    # Messages inside Grox threads are intentionally NOT treated
    # as new orders. They are handled by the active wait_for()
    # listeners in process_order().
    if message.channel.id != CLIENT_CHANNEL_ID:
        return

    if not is_order_message(
        message.content
    ):
        return

    if message.id in active_orders:
        return

    if (
        active_clients.get(message.author.id, 0)
        >= MAX_ACTIVE_ORDERS_PER_USER
    ):
        try:
            await message.reply(
                f"⏳ У вас уже відкрито максимум замовлень "
                f"({MAX_ACTIVE_ORDERS_PER_USER}). Завершіть або "
                f"дочекайтеся закінчення поточних і спробуйте ще раз."
            )
        except Exception:
            pass
        return

    print(
        f"[ORDER DETECTED] user_id={message.author.id} "
        f"message_id={message.id} "
        f"message_length={len(message.content)}"
    )

    task = asyncio.create_task(process_order(message))
    processing_tasks.add(task)
    task.add_done_callback(processing_tasks.discard)


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

discord_ready = False


async def health_handler(request):
    # Liveness: keep Render informed that the process is alive even if
    # Discord temporarily rate-limits the Render IP.
    return web.json_response({
        "status": "ok",
        "service": "grox",
        "discord_ready": discord_ready,
    })


async def readiness_handler(request):
    payload = {
        "status": "ok" if discord_ready else "starting",
        "discord_ready": discord_ready,
    }
    return web.json_response(
        payload,
        status=200 if discord_ready else 503,
    )


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/readyz", readiness_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"[HEALTH] Server listening on 0.0.0.0:{PORT}")
    print("[HEALTH] Liveness: / and /healthz; readiness: /readyz")
    return runner


def discord_retry_delay(error, attempt: int) -> tuple[int, str]:
    """Use cautious delays; Cloudflare 1015 needs a long pause."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after")

    try:
        header_delay = max(0, int(float(retry_after))) if retry_after else 0
    except (TypeError, ValueError):
        header_delay = 0

    error_text = str(error).casefold()
    is_cloudflare_1015 = (
        "1015" in error_text
        or ("cloudflare" in error_text and "rate limited" in error_text)
        or ("cloudflare" in error_text and "too many requests" in error_text)
    )

    if is_cloudflare_1015:
        delay = max(header_delay, min(3600, 1800 * (2 ** min(attempt - 1, 1))))
        reason = "Cloudflare 1015 — довга пауза, щоб не посилювати блокування"
    elif getattr(error, "status", None) == 429 or "429" in error_text:
        delay = max(header_delay, min(900, 60 * (2 ** min(attempt - 1, 4))))
        reason = "Discord HTTP 429 — дотримуюся обмеження запитів"
    elif getattr(error, "status", None) in (401, 403):
        delay = max(header_delay, 3600)
        reason = "Discord відхилив авторизацію/доступ — довга пауза"
    else:
        delay = max(header_delay, min(300, 15 * (2 ** min(attempt - 1, 4))))
        reason = "тимчасова помилка підключення Discord"

    return delay, reason


# ============================================================
# MAIN
# ============================================================

async def main():
    global discord_ready

    print("🚀 Starting Grox...")
    health_runner = await start_health_server()
    attempt = 0
    loop = asyncio.get_running_loop()

    def request_shutdown():
        print("[SHUTDOWN] Signal received; closing Discord client gracefully.")
        if not bot.is_closed():
            asyncio.create_task(bot.close())

    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(shutdown_signal, request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Signal handlers may be unavailable on non-Unix test platforms.
            pass

    try:
        await initialize_database()
        while True:
            attempt += 1
            discord_ready = False
            print(f"[DISCORD] Підключення до Discord (спроба {attempt})...")

            try:
                # discord.py handles normal gateway reconnects. This loop
                # handles errors during initial login/startup.
                await bot.start(DISCORD_TOKEN, reconnect=True)

                if bot.is_closed():
                    print("[DISCORD] Bot client closed; stopping supervisor.")
                    break

                raise RuntimeError(
                    "bot.start() завершився несподівано без закриття клієнта."
                )

            except asyncio.CancelledError:
                raise

            except (
                discord.LoginFailure,
                discord.PrivilegedIntentsRequired,
            ) as error:
                # LoginFailure is NOT a subclass of HTTPException, so it
                # used to fall into the generic handler and retry every
                # few minutes. It is a configuration problem.
                discord_ready = False
                delay = 3600
                print(
                    f"[DISCORD] Помилка конфігурації {type(error).__name__}. "
                    "Перевір DISCORD_TOKEN та Privileged Gateway Intents "
                    "(Message Content Intent) у Discord Developer Portal. "
                    f"Наступна спроба через {delay} секунд."
                )
                await asyncio.sleep(delay)

            except discord.HTTPException as error:
                discord_ready = False
                delay, reason = discord_retry_delay(error, attempt)
                # Do not dump Cloudflare's full HTML challenge page into logs.
                print(
                    f"[DISCORD] HTTP-помилка "
                    f"{getattr(error, 'status', 'unknown')}: "
                    f"{type(error).__name__}."
                )
                print(f"[DISCORD] {reason}. Наступна спроба через {delay} секунд.")
                await asyncio.sleep(delay)

            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as error:
                discord_ready = False
                delay = min(300, 15 * (2 ** min(attempt - 1, 4)))
                print(
                    f"[DISCORD] Мережева помилка {type(error).__name__}: "
                    f"{safe_exception(error)}. Повтор через {delay} секунд."
                )
                await asyncio.sleep(delay)

            except Exception as error:
                discord_ready = False
                delay = min(300, 30 * (2 ** min(attempt - 1, 3)))
                print(
                    f"[DISCORD] Непередбачена помилка {type(error).__name__}: "
                    f"{safe_exception(error)}. Повтор через {delay} секунд."
                )
                print("[TRACEBACK OMITTED] Деталі помилки приховано, щоб виключити витік секретів.")
                await asyncio.sleep(delay)

    finally:
        discord_ready = False
        if not bot.is_closed():
            await bot.close()

        # Render sends SIGTERM before stopping an instance. Give active orders
        # a short grace period to persist their state, then cancel leftovers.
        pending_tasks = set(processing_tasks)
        if pending_tasks:
            _, pending_tasks = await asyncio.wait(pending_tasks, timeout=20)
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

        for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(shutdown_signal)
            except (NotImplementedError, RuntimeError):
                pass

        await health_runner.cleanup()
        await close_database()


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
