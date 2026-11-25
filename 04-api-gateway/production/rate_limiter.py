"""
Redis-backed Rate Limiter for production API Gateway.
Implements distributed sliding window via Redis sorted set.
"""
import hashlib
import os
import time
import uuid

from fastapi import HTTPException
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cutoff = now - (window * 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = math.max(1, math.ceil((tonumber(oldest[2]) + (window * 1000) - now) / 1000))
    return {0, count, retry_after}
end

redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window + 1)
return {1, count + 1, 0}
"""


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check(self, user_id: str) -> dict:
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        key = f"rate-limit:gw:{self.max_requests}:{self.window_seconds}:{user_hash}"
        now_ms = int(time.time() * 1000)
        req_id = uuid.uuid4().hex

        try:
            allowed, current_count, retry_after = _redis.eval(
                _SLIDING_WINDOW_SCRIPT,
                1,
                key,
                now_ms,
                self.window_seconds,
                self.max_requests,
                req_id,
            )
        except redis.exceptions.RedisError as exc:
            raise HTTPException(
                status_code=503,
                detail="Rate limiting storage is unavailable",
            ) from exc

        reset_at = int(time.time()) + self.window_seconds
        remaining = max(0, self.max_requests - int(current_count))

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": self.max_requests,
                    "window_seconds": self.window_seconds,
                    "retry_after_seconds": int(retry_after),
                },
                headers={
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(int(retry_after)),
                },
            )

        return {
            "limit": self.max_requests,
            "remaining": remaining,
            "reset_at": reset_at,
        }

    def get_stats(self, user_id: str) -> dict:
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        key = f"rate-limit:gw:{self.max_requests}:{self.window_seconds}:{user_hash}"
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - (self.window_seconds * 1000)
        try:
            _redis.zremrangebyscore(key, "-inf", cutoff)
            active = _redis.zcard(key)
        except redis.exceptions.RedisError:
            active = 0
        return {
            "requests_in_window": active,
            "limit": self.max_requests,
            "remaining": max(0, self.max_requests - active),
        }


rate_limiter_user = RateLimiter(max_requests=10, window_seconds=60)
rate_limiter_admin = RateLimiter(max_requests=100, window_seconds=60)
