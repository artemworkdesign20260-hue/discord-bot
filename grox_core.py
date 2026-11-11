"""Small, dependency-free helpers for Grox startup and output validation."""

import asyncio
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def is_cloudflare_1015(status, error_text=""):
    """Identify Cloudflare's 1015 page without logging its response body."""
    if status == 1015:
        return True
    text = str(error_text or "")
    return bool(
        re.search(
            r"(?:error\s*1015|1015.{0,80}cloudflare|cloudflare.{0,80}1015|cf-error-code\s*[=:]\s*1015)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    )


def should_retry_discord_start(
    status=None,
    *,
    cloudflare_1015_error=False,
    network_error=False,
    rate_limited=False,
):
    """Retry only rate limits, server errors, and transport failures."""
    if cloudflare_1015_error or network_error or rate_limited:
        return True
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return status == 429 or 500 <= status < 600


def extract_discord_retry_after(error):
    """Read Retry-After from discord.py exceptions without exposing the body."""
    value = getattr(error, "retry_after", None)
    if value is not None:
        return value

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get("Retry-After")
    except (AttributeError, TypeError):
        value = None
    if value is not None:
        return value

    body = getattr(error, "text", None)
    if body:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            return payload.get("retry_after")
    return None


def discord_start_retry_delay(
    retry_after,
    attempt,
    *,
    cloudflare_1015_error=False,
    now=None,
):
    """Respect provider Retry-After; otherwise use a cautious capped backoff."""
    parsed_delay = None
    if retry_after is not None:
        try:
            parsed_delay = float(str(retry_after).strip())
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                reference = now or datetime.now(timezone.utc)
                if reference.tzinfo is None:
                    reference = reference.replace(tzinfo=timezone.utc)
                parsed_delay = (retry_at - reference).total_seconds()
            except (TypeError, ValueError, OverflowError):
                parsed_delay = None

    if parsed_delay is not None:
        parsed_delay = max(0.0, parsed_delay)
        if cloudflare_1015_error:
            return max(300.0, parsed_delay)
        return max(1.0, parsed_delay)

    exponent = max(0, int(attempt) - 1)
    if cloudflare_1015_error:
        return min(3600.0, 300.0 * (2 ** min(exponent, 4)))
    return min(300.0, 5.0 * (2 ** min(exponent, 6)))


async def run_discord_start_with_retries(
    start,
    token,
    *,
    sleep,
    network_error,
    on_retry=None,
    jitter=None,
):
    """Retry transient failures from the first Discord login, not bad credentials."""
    attempt = 0
    while True:
        try:
            return await start(token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status = getattr(error, "status", None)
            error_text = getattr(error, "text", "") or ""
            cloudflare_1015_error = is_cloudflare_1015(status, error_text)
            is_network_error = bool(network_error(error))
            rate_limited = type(error).__name__ == "RateLimited"

            if not should_retry_discord_start(
                status,
                cloudflare_1015_error=cloudflare_1015_error,
                network_error=is_network_error,
                rate_limited=rate_limited,
            ):
                raise

            attempt += 1
            retry_after = extract_discord_retry_after(error)
            delay = discord_start_retry_delay(
                retry_after,
                attempt,
                cloudflare_1015_error=cloudflare_1015_error,
            )
            if retry_after is None and jitter is not None:
                delay += max(0.0, float(jitter(min(10.0, delay * 0.1))))

            if cloudflare_1015_error:
                category = "cloudflare-1015"
            elif status == 429 or rate_limited:
                category = "rate-limit"
            else:
                category = "transient"
            if on_retry is not None:
                on_retry(category, status or "n/a", attempt, delay)
            await sleep(delay)


_RETIRED_GROQ_MODEL = re.compile(r"\bmixtral-8x7b-32768\b", re.IGNORECASE)
_MOCK_IMPLEMENTATION_PATTERNS = (
    re.compile(r"\bsimulate_(?:search|fetch|request|discovery)\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:SAMPLE_COMMUNITIES|MockGenerator|dummy_(?:admin|guild|user|community))\b", re.IGNORECASE),
    re.compile(r"https?://discord\.gg/fake[^\s\"']*", re.IGNORECASE),
    re.compile(r"\b100000000000000000\s*\+\s*\w+", re.IGNORECASE),
)
_BOT2_DIRECT_MESSAGE = re.compile(
    r"\b(?:user|member|admin)\.send\s*\(|\bcreate_dm\s*\(",
    re.IGNORECASE,
)


def generated_code_issues(files, *, bot2=False):
    """Reject known dead models and clear mock/stub output before publication."""
    issues = []
    for filename, content in files.items():
        if _RETIRED_GROQ_MODEL.search(content or ""):
            issues.append(f"retired_groq_model:{filename}")

    source = files.get("main.py", "") or ""
    if any(pattern.search(source) for pattern in _MOCK_IMPLEMENTATION_PATTERNS):
        issues.append("mock_or_fabricated_implementation:main.py")
    if bot2 and _BOT2_DIRECT_MESSAGE.search(source):
        issues.append("bot2_unsolicited_direct_message_path:main.py")
    return issues
