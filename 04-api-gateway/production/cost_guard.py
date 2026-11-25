"""
Redis-backed Cost Guard for production API Gateway.
Tracks LLM spending in Redis with distributed atomic increments and budget enforcement.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import time

from fastapi import HTTPException
import redis

logger = None
try:
    import logging
    logger = logging.getLogger(__name__)
except Exception:
    pass

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)

PRICE_PER_1K_INPUT_TOKENS = 0.00015
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006


@dataclass
class UsageRecord:
    user_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 0
    day: str = ""

    @property
    def total_cost_usd(self) -> float:
        input_cost = (self.input_tokens / 1000) * PRICE_PER_1K_INPUT_TOKENS
        output_cost = (self.output_tokens / 1000) * PRICE_PER_1K_OUTPUT_TOKENS
        return round(input_cost + output_cost, 6)


class CostGuard:
    def __init__(
        self,
        daily_budget_usd: float = 1.0,
        global_daily_budget_usd: float = 10.0,
        warn_at_pct: float = 0.8,
    ):
        self.daily_budget_usd = float(os.getenv("DAILY_BUDGET_USD", daily_budget_usd))
        self.global_daily_budget_usd = float(os.getenv("GLOBAL_DAILY_BUDGET_USD", global_daily_budget_usd))
        self.warn_at_pct = warn_at_pct

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _user_key(self, user_id: str, today: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"cost:gw:user:{today}:{digest}"

    def _global_key(self, today: str) -> str:
        return f"cost:gw:global:{today}"

    def check_budget(self, user_id: str) -> None:
        today = self._today()
        user_key = self._user_key(user_id, today)
        global_key = self._global_key(today)

        try:
            pipe = _redis.pipeline()
            pipe.hget(global_key, "total_cost_usd")
            pipe.hget(user_key, "total_cost_usd")
            global_cost_raw, user_cost_raw = pipe.execute()
        except redis.exceptions.RedisError as exc:
            raise HTTPException(status_code=503, detail="Cost tracking storage is unavailable") from exc

        global_cost = float(global_cost_raw or 0.0)
        user_cost = float(user_cost_raw or 0.0)

        if global_cost >= self.global_daily_budget_usd:
            if logger:
                logger.critical(f"GLOBAL BUDGET EXCEEDED: ${global_cost:.4f}")
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable due to budget limits. Try again tomorrow.",
            )

        if user_cost >= self.daily_budget_usd:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Daily budget exceeded",
                    "used_usd": round(user_cost, 6),
                    "budget_usd": self.daily_budget_usd,
                    "resets_at": "midnight UTC",
                },
            )

        if user_cost >= self.daily_budget_usd * self.warn_at_pct:
            if logger:
                logger.warning(
                    f"User {user_id} at {user_cost / self.daily_budget_usd * 100:.0f}% budget"
                )

    def record_usage(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> UsageRecord:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")

        today = self._today()
        user_key = self._user_key(user_id, today)
        global_key = self._global_key(today)

        cost_increment = (
            (input_tokens / 1000) * PRICE_PER_1K_INPUT_TOKENS
            + (output_tokens / 1000) * PRICE_PER_1K_OUTPUT_TOKENS
        )

        try:
            pipe = _redis.pipeline()
            # User hash
            pipe.hincrby(user_key, "input_tokens", input_tokens)
            pipe.hincrby(user_key, "output_tokens", output_tokens)
            pipe.hincrby(user_key, "request_count", 1)
            pipe.hincrbyfloat(user_key, "total_cost_usd", cost_increment)
            pipe.expire(user_key, 86400 * 2)

            # Global hash
            pipe.hincrbyfloat(global_key, "total_cost_usd", cost_increment)
            pipe.expire(global_key, 86400 * 2)

            res = pipe.execute()
        except redis.exceptions.RedisError as exc:
            raise HTTPException(status_code=503, detail="Cost tracking storage is unavailable") from exc

        return UsageRecord(
            user_id=user_id,
            input_tokens=int(res[0]),
            output_tokens=int(res[1]),
            request_count=int(res[2]),
            day=today,
        )

    def get_usage(self, user_id: str) -> dict:
        today = self._today()
        user_key = self._user_key(user_id, today)
        try:
            data = _redis.hgetall(user_key)
        except redis.exceptions.RedisError as exc:
            raise HTTPException(status_code=503, detail="Cost tracking storage is unavailable") from exc
        return {
            "user_id": user_id,
            "day": today,
            "input_tokens": int(data.get("input_tokens", 0)),
            "output_tokens": int(data.get("output_tokens", 0)),
            "request_count": int(data.get("request_count", 0)),
            "cost_usd": float(data.get("total_cost_usd", 0.0)),
            "budget_usd": self.daily_budget_usd,
        }

    def get_global_cost(self) -> float:
        today = self._today()
        global_key = self._global_key(today)
        try:
            val = _redis.hget(global_key, "total_cost_usd")
            return float(val or 0.0)
        except redis.exceptions.RedisError:
            return 0.0


cost_guard = CostGuard()
