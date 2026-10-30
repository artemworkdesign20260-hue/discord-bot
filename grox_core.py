import ast
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_BOT_RE = re.compile(
    r"(?<!\w)(?:discord[-\s]*(?:bot|бот)|telegram[-\s]*(?:bot|бот)|bots?|бот(?:а|у|ом|и|ів|ами)?)(?!\w)",
    re.IGNORECASE,
)
_SITE_RE = re.compile(
    r"(?<!\w)(?:web\s*site|website|web|site|сайт(?:а|у|ом|і|и|ів)?|веб[-\s]*сайт(?:а|у|ом)?|landing|лендинг(?:у|а|ом)?)(?!\w)",
    re.IGNORECASE,
)
_BUDGET_RE = re.compile(
    r"(?:[$€£₴]\s*\d[\d\s,.]*|\d[\d\s,.]*\s*(?:[$€£₴]|usd|eur|uah|gbp|грн(?:ив(?:ня|ні|ень)?)?|долар(?:ів|и)?|євро|euro|dollars?))",
    re.IGNORECASE,
)
_BUDGET_UNDECIDED_RE = re.compile(
    r"(?:не\s+визначив(?:ся|лась|ся)?|ще\s+не\s+вирішив(?:ся|лась)?|не\s+знаю\s+бюджет|без\s+бюджету|поки\s+немає\s+бюджету|бюджет\s+(?:гнучкий|не\s+визначений|не\s+важливий)|(?:no|not\s+sure\s+about)\s+budget|budget\s+is\s+flexible)",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[opsur]_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
)
_REDACTION_PATTERNS = (
    (re.compile(r"\b(?:[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[opsur]_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)(\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[\"']?)[^\s\"',;}{]+"), r"\1[REDACTED]"),
)


def classify_project_type(text: str) -> str:
    """Classify from the client's request, not an LLM-rewritten summary."""
    normalized = (text or "").casefold()
    has_bot = bool(_BOT_RE.search(normalized))
    has_site = bool(_SITE_RE.search(normalized))
    if has_bot and has_site:
        return "mixed"
    if has_bot:
        if re.search(r"(?<!\w)discord(?!\w)", normalized):
            return "discord_bot"
        if re.search(r"(?<!\w)(?:telegram|телеграм)(?!\w)", normalized):
            return "telegram_bot"
        return "software_bot"
    if has_site:
        return "website"
    return "unsupported"


def extract_budget(text: str) -> str | None:
    match = _BUDGET_RE.search(text or "")
    if match:
        return re.sub(r"\s+", " ", match.group(0)).strip()
    undecided = _BUDGET_UNDECIDED_RE.search(text or "")
    return undecided.group(0).strip() if undecided else None


def has_budget_information(text: str) -> bool:
    return extract_budget(text) is not None


def retry_delay(retry_after: str | None, attempt: int, *, base: float = 1.0, maximum: float = 60.0, now: datetime | None = None) -> float:
    """Respect numeric or HTTP-date Retry-After; otherwise use capped backoff."""
    if retry_after:
        try:
            return min(maximum, max(0.0, float(retry_after.strip())))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                reference = now or datetime.now(timezone.utc)
                if reference.tzinfo is None:
                    reference = reference.replace(tzinfo=timezone.utc)
                return min(maximum, max(0.0, (retry_at - reference).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(maximum, base * (2 ** max(0, attempt - 1)))


def is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def is_retryable_deploy_status(status: int) -> bool:
    # Deployment creation is a POST; only explicit rate-limit rejection is
    # retried automatically to avoid duplicate deployments after ambiguous 5xx.
    return status == 429


def is_infrastructure_qa_failure(result: dict) -> bool:
    return result.get("category") == "infrastructure"


def sanitize_sensitive_text(text: object) -> str:
    safe = str(text)
    for pattern, replacement in _REDACTION_PATTERNS:
        safe = pattern.sub(replacement, safe)
    return safe


def contains_secret_like_value(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _SECRET_PATTERNS)


def validate_generated_files(
    files: dict[str, str],
    expected_platform: str | None = None
) -> list[str]:
    """Static checks only: do not execute model-generated code."""
    issues = []
    for required in ("main.py", "requirements.txt"):
        if not files.get(required, "").strip():
            issues.append(f"missing_or_empty:{required}")
    source = files.get("main.py", "")
    if source.strip():
        try:
            ast.parse(source, filename="main.py")
        except SyntaxError as error:
            issues.append(f"syntax_error:main.py:{error.lineno or 0}")
    if expected_platform == "Discord":
        if not re.search(r"\.\s*is_ready\s*\(", source):
            issues.append("missing_discord_readiness_check")
        if not re.search(r"\b503\b", source):
            issues.append("missing_not_ready_status")
        if not re.search(r"['\"]/?readyz['\"]", source):
            issues.append("missing_discord_readiness_endpoint")
        if not re.search(r"['\"]/?healthz['\"]", source):
            issues.append("missing_render_liveness_endpoint")
    for filename, content in files.items():
        if contains_secret_like_value(content):
            issues.append(f"possible_secret:{filename}")
    return issues
