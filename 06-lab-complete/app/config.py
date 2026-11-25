"""12-Factor application settings loaded from environment variables."""
from dataclasses import dataclass
import os


@dataclass
class Settings:
    port: int
    environment: str
    agent_api_key: str
    redis_url: str
    rate_limit_per_minute: int
    daily_budget_usd: float
    budget_period: str
    allowed_origins: list[str]


def load_settings() -> Settings:
    daily_budget = os.getenv("DAILY_BUDGET_USD")
    monthly_budget = os.getenv("MONTHLY_BUDGET_USD")
    budget_value = daily_budget if daily_budget is not None else monthly_budget
    budget_period = "daily" if daily_budget is not None or monthly_budget is None else "monthly"

    rate_limit = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    budget = float(budget_value if budget_value is not None else "1.0")
    if rate_limit < 1:
        raise ValueError("RATE_LIMIT_PER_MINUTE must be greater than zero")
    if budget < 0:
        raise ValueError("DAILY_BUDGET_USD / MONTHLY_BUDGET_USD cannot be negative")

    raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    allowed_origins = [o.strip() for o in raw_origins if o.strip() and o.strip() != "*"]

    return Settings(
        port=int(os.getenv("PORT", "8000")),
        environment=os.getenv("ENVIRONMENT", "development"),
        agent_api_key=os.getenv("AGENT_API_KEY", ""),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        rate_limit_per_minute=rate_limit,
        daily_budget_usd=budget,
        budget_period=budget_period,
        allowed_origins=allowed_origins or ["http://localhost:3000"],
    )


settings = load_settings()
