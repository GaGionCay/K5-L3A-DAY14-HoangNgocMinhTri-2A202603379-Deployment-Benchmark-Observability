"""
Cost guard (tiêu chí 8).

Yêu cầu:
  • Cộng dồn chi phí LLM (tính theo token) vào Redis
  • Tổng chi phí vượt DAILY_BUDGET_USD (hoặc MONTHLY_BUDGET_USD) → HTTP 402
"""
from datetime import datetime, timedelta, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r


PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def _budget_key_and_ttl() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    if settings.budget_period == "monthly":
        key = f"cost:monthly:{now:%Y-%m}"
        next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        ttl = max(1, int((next_month - now).total_seconds()) + 86400)
    else:
        key = f"cost:daily:{now:%Y-%m-%d}"
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl = max(1, int((midnight - now).total_seconds()) + 86400)
    return key, ttl


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    key, _ = _budget_key_and_ttl()
    try:
        spent = float(r.get(key) or 0)
    except redis.exceptions.RedisError as exc:
        raise HTTPException(status_code=503, detail="Cost tracking storage is unavailable") from exc
    if spent >= settings.daily_budget_usd:
        raise HTTPException(status_code=402, detail="Configured spending budget has been exhausted")


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    cost = (
        input_tokens * PRICE_INPUT_PER_1K + output_tokens * PRICE_OUTPUT_PER_1K
    ) / 1000
    key, ttl = _budget_key_and_ttl()
    try:
        r.incrbyfloat(key, cost)
        r.expire(key, ttl)
    except redis.exceptions.RedisError as exc:
        raise HTTPException(status_code=503, detail="Cost tracking storage is unavailable") from exc
    return cost
