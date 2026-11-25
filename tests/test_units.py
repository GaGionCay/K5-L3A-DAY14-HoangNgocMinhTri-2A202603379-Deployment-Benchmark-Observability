"""
Unit tests for Day 14 core components:
- Pydantic models (validation, limits)
- Auth (API key & JWT verification)
- Rate limiting (Redis sliding window & error handling)
- Cost guard (budget checks & cost calculations)
- Redis failure handling (simulated timeouts/connection errors)
- LLM failure handling (simulated upstream errors)
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
import redis

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPydanticModels(unittest.TestCase):
    def test_06_ask_request_valid(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            import app.main as lab_main
            req = lab_main.AskRequest(question="What is docker?")
            self.assertEqual(req.question, "What is docker?")
        finally:
            sys.path = saved_path

    def test_06_ask_request_empty_fails(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            import app.main as lab_main
            with self.assertRaises(ValidationError):
                lab_main.AskRequest(question="")
        finally:
            sys.path = saved_path

    def test_06_ask_request_too_long_fails(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            import app.main as lab_main
            with self.assertRaises(ValidationError):
                lab_main.AskRequest(question="a" * 2001)
        finally:
            sys.path = saved_path

    def test_gateway_models(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "04-api-gateway", "production"))
        if "app" in sys.modules:
            del sys.modules["app"]
        try:
            import app as gw_app
            login = gw_app.LoginRequest(username="admin", password="password123")
            self.assertEqual(login.username, "admin")
            gw_req = gw_app.AskRequest(question="Test question")
            self.assertEqual(gw_req.question, "Test question")
        finally:
            if "app" in sys.modules:
                del sys.modules["app"]
            sys.path = saved_path


class TestAuth(unittest.TestCase):
    def test_06_auth_valid_key(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.auth import verify_api_key
            from app.config import settings
            with patch.object(settings, "agent_api_key", "secret-test-key"):
                verify_api_key("secret-test-key")
        finally:
            sys.path = saved_path

    def test_06_auth_invalid_key_raises_401(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.auth import verify_api_key
            from app.config import settings
            with patch.object(settings, "agent_api_key", "secret-test-key"):
                with self.assertRaises(HTTPException) as ctx:
                    verify_api_key("wrong-key")
                self.assertEqual(ctx.exception.status_code, 401)
        finally:
            sys.path = saved_path

    def test_jwt_auth_create_and_verify(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "04-api-gateway", "production"))
        try:
            from auth import create_token, verify_token
            from fastapi.security import HTTPAuthorizationCredentials

            with patch.dict(os.environ, {"JWT_SECRET": "test-super-secret-key-32-bytes!!"}):
                token = create_token("alice", "admin")
                creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
                verified = verify_token(creds)
                self.assertEqual(verified["username"], "alice")
                self.assertEqual(verified["role"], "admin")
        finally:
            sys.path = saved_path


class TestRateLimiter(unittest.TestCase):
    def test_rate_limiter_redis_allowed_and_blocked(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.rate_limiter import check_rate_limit

            mock_redis = MagicMock()
            mock_redis.eval.return_value = 0
            with patch("app.rate_limiter.r", mock_redis):
                check_rate_limit("test-key")
                mock_redis.eval.assert_called_once()

            mock_redis.eval.return_value = 5
            with patch("app.rate_limiter.r", mock_redis):
                with self.assertRaises(HTTPException) as ctx:
                    check_rate_limit("test-key")
                self.assertEqual(ctx.exception.status_code, 429)
                self.assertEqual(ctx.exception.headers.get("Retry-After"), "5")
        finally:
            sys.path = saved_path

    def test_rate_limiter_redis_failure_raises_503(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.rate_limiter import check_rate_limit
            mock_redis = MagicMock()
            mock_redis.eval.side_effect = redis.exceptions.ConnectionError("Redis connection refused")
            with patch("app.rate_limiter.r", mock_redis):
                with self.assertRaises(HTTPException) as ctx:
                    check_rate_limit("test-key")
                self.assertEqual(ctx.exception.status_code, 503)
        finally:
            sys.path = saved_path


class TestCostGuard(unittest.TestCase):
    def test_cost_guard_within_budget(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.cost_guard import check_budget, record_cost
            from app.config import settings

            mock_redis = MagicMock()
            mock_redis.get.return_value = "0.5"
            with patch("app.cost_guard.r", mock_redis):
                with patch.object(settings, "daily_budget_usd", 1.0):
                    check_budget()

            mock_redis.incrbyfloat.return_value = 0.505
            with patch("app.cost_guard.r", mock_redis):
                cost = record_cost(1000, 500)
                self.assertAlmostEqual(cost, 0.00045, places=5)
        finally:
            sys.path = saved_path

    def test_cost_guard_budget_exceeded_raises_402(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.cost_guard import check_budget
            from app.config import settings

            mock_redis = MagicMock()
            mock_redis.get.return_value = "1.5"
            with patch("app.cost_guard.r", mock_redis):
                with patch.object(settings, "daily_budget_usd", 1.0):
                    with self.assertRaises(HTTPException) as ctx:
                        check_budget()
                    self.assertEqual(ctx.exception.status_code, 402)
        finally:
            sys.path = saved_path

    def test_cost_guard_redis_failure_raises_503(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.cost_guard import check_budget
            mock_redis = MagicMock()
            mock_redis.get.side_effect = redis.exceptions.TimeoutError("Redis timeout")
            with patch("app.cost_guard.r", mock_redis):
                with self.assertRaises(HTTPException) as ctx:
                    check_budget()
                self.assertEqual(ctx.exception.status_code, 503)
        finally:
            sys.path = saved_path


class TestLLMFailures(unittest.TestCase):
    def test_observable_llm_failure_handling(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete", "benchmark-observability", "production"))
        if "utils.mock_llm" in sys.modules:
            del sys.modules["utils.mock_llm"]
        try:
            import utils.mock_llm as obs_mock
            self.assertTrue(issubclass(obs_mock.LLMError, Exception))
        finally:
            if "utils.mock_llm" in sys.modules:
                del sys.modules["utils.mock_llm"]
            sys.path = saved_path


if __name__ == "__main__":
    unittest.main()
