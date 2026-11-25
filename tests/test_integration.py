"""
Integration tests for Day 14 deployment topology:
- Validates Dockerfile structure & parameters across contexts
- Validates docker-compose files and service definitions (Nginx, Redis, Agent)
- Verifies graceful shutdown / SIGTERM semantics and timeout bounds
- Tests simulated Redis restart / reconnection semantics
"""
import inspect
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDockerAndComposeIntegration(unittest.TestCase):
    def test_production_dockerfiles_have_port_and_non_root(self):
        dockerfiles = [
            os.path.join(PROJECT_ROOT, "02-docker", "production", "Dockerfile"),
            os.path.join(PROJECT_ROOT, "05-scaling-reliability", "production", "Dockerfile"),
            os.path.join(PROJECT_ROOT, "06-lab-complete", "Dockerfile"),
            os.path.join(PROJECT_ROOT, "06-lab-complete", "benchmark-observability", "production", "Dockerfile"),
        ]

        for df_path in dockerfiles:
            self.assertTrue(os.path.exists(df_path), f"Dockerfile missing: {df_path}")
            with open(df_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("USER ", content, f"Missing non-root USER in {df_path}")
            self.assertIn("PORT", content, f"Missing PORT env support in {df_path}")
            from_count = content.count("FROM ")
            self.assertGreaterEqual(from_count, 2, f"Expected multi-stage build in {df_path}")

    def test_lab_complete_compose_structure(self):
        compose_path = os.path.join(PROJECT_ROOT, "06-lab-complete", "docker-compose.yml")
        self.assertTrue(os.path.exists(compose_path))
        with open(compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        services = data.get("services", {})
        self.assertIn("agent", services)
        self.assertIn("redis", services)

        agent = services["agent"]
        self.assertIn("build", agent)
        self.assertIn("REDIS_URL", agent.get("environment", {}))
        self.assertEqual(agent.get("stop_grace_period"), "10s")

        redis_svc = services["redis"]
        self.assertTrue(str(redis_svc.get("image", "")).startswith("redis:7-alpine"))
        self.assertIn("healthcheck", redis_svc)

    def test_observability_compose_structure_and_nginx(self):
        compose_path = os.path.join(
            PROJECT_ROOT, "06-lab-complete", "benchmark-observability", "production", "docker-compose.yml"
        )
        self.assertTrue(os.path.exists(compose_path))
        with open(compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        services = data.get("services", {})
        for required in ["agent", "nginx", "prometheus", "grafana", "jaeger"]:
            self.assertIn(required, services, f"Missing service {required}")

        nginx_conf_path = os.path.join(
            PROJECT_ROOT, "06-lab-complete", "benchmark-observability", "production", "nginx", "nginx.conf"
        )
        self.assertTrue(os.path.exists(nginx_conf_path))
        with open(nginx_conf_path, "r", encoding="utf-8") as f:
            nginx_conf = f.read()

        self.assertIn("location = /metrics", nginx_conf)
        self.assertIn("return 404;", nginx_conf)

        grafana_env = services["grafana"].get("environment", [])
        env_dict = {}
        for item in grafana_env:
            if "=" in item:
                k, v = item.split("=", 1)
                env_dict[k] = v
        self.assertNotEqual(env_dict.get("GF_SECURITY_ADMIN_PASSWORD"), "admin")
        self.assertNotEqual(env_dict.get("GF_AUTH_ANONYMOUS_ENABLED"), "true")


class TestOperationalResilience(unittest.TestCase):
    def test_redis_restart_reconnect_simulation(self):
        import redis
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.redis_client import r, redis_healthy

            with patch.object(r, "ping", side_effect=redis.exceptions.ConnectionError("Redis restarting")):
                self.assertFalse(redis_healthy())

            with patch.object(r, "ping", return_value=True):
                self.assertTrue(redis_healthy())
        finally:
            sys.path = saved_path

    def test_sigterm_graceful_shutdown_lifespan(self):
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "06-lab-complete"))
        try:
            from app.main import lifespan
            self.assertTrue(hasattr(lifespan, "__wrapped__"))
            self.assertTrue(inspect.isasyncgenfunction(lifespan.__wrapped__))
        finally:
            sys.path = saved_path


if __name__ == "__main__":
    unittest.main()
