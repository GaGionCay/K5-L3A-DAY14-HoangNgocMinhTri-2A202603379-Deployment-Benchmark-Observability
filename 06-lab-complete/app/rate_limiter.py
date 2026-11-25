"""
Rate limiting (tiêu chí 7).

Yêu cầu:
  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429, nên kèm header Retry-After
  • State lưu trong Redis để mọi worker / instance dùng chung và vẫn còn sau khi
    restart agent (script chấm sẽ restart agent để kiểm tra)
"""
import hashlib
import math
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local cutoff = now - 60000
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return math.max(1, math.ceil((tonumber(oldest[2]) + 60000 - now) / 1000))
end
redis.call('ZADD', key, now, ARGV[3])
redis.call('EXPIRE', key, 61)
return 0
"""


def check_rate_limit(key: str) -> None:
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    try:
        retry_after = int(r.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            f"rate-limit:{key_digest}",
            int(time.time() * 1000),
            settings.rate_limit_per_minute,
            uuid.uuid4().hex,
        ))
    except redis.exceptions.RedisError as exc:
        raise HTTPException(status_code=503, detail="Rate limiting storage is unavailable") from exc

    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
