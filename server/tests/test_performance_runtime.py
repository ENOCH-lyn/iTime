import json
import os
import subprocess
import tomllib
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
COMPOSE = ROOT / "compose.performance.yaml"


def performance_environment() -> dict[str, str]:
    key = Fernet.generate_key().decode()
    return {
        **os.environ,
        "ITIME_PERF_RUN_ID": "abc12345",
        "ITIME_PERF_DATABASE": "itime_perf_abc12345",
        "ITIME_PERF_POSTGRES_PASSWORD": "synthetic-only",
        "ITIME_PERF_ENCRYPTION_KEY": key,
        "ITIME_PERF_AI_ENCRYPTION_KEY": key,
        "ITIME_PERF_CALENDAR_KEY": key,
        "ITIME_PERF_EXPORT_KEY": key,
        "ITIME_PERF_UPSTREAM_SMTP_PORT": "19025",
    }


def load_compose_config() -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=performance_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_performance_runtime_is_pinned_and_excludes_local_state():
    project = tomllib.loads((SERVER / "pyproject.toml").read_text())
    assert any(
        value.startswith("locust>=") and value.endswith(",<3")
        for value in project["dependency-groups"]["dev"]
    )

    dockerfile = (SERVER / "Dockerfile.performance").read_text()
    assert (
        "python:3.13.9-slim-bookworm@"
        "sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34"
    ) in dockerfile
    assert "uv==0.11.29" in dockerfile
    assert "USER itime" in dockerfile

    ignored = set((SERVER / ".dockerignore").read_text().splitlines())
    assert {
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "dist",
        "*.egg-info",
        "*.log",
        "tests",
    } <= ignored
    assert "performance-results/" in (ROOT / ".gitignore").read_text().splitlines()


def test_performance_compose_fixes_services_processes_and_connection_budget():
    config = load_compose_config()
    services = config["services"]
    assert isinstance(services, dict)
    required = {
        "postgres",
        "migrate",
        "api",
        "ai-worker",
        "mail-worker",
        "subscription-worker",
        "public-link-worker",
        "export-worker",
    }
    assert required == set(services)

    postgres = services["postgres"]
    assert postgres["image"] == (
        "postgres:18-alpine@"
        "sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
    )
    assert "max_connections=50" in postgres["command"]

    api = services["api"]
    command = api["command"]
    assert command[command.index("--workers") + 1] == "2"
    assert api["environment"]["ITIME_DB_POOL_SIZE"] == "4"
    assert api["environment"]["ITIME_DB_MAX_OVERFLOW"] == "1"

    ai_worker = services["ai-worker"]
    assert ai_worker["deploy"]["replicas"] == 2
    assert ai_worker["environment"]["ITIME_DB_POOL_SIZE"] == "1"
    assert ai_worker["environment"]["ITIME_DB_MAX_OVERFLOW"] == "0"

    serial_workers = {
        "mail-worker",
        "subscription-worker",
        "public-link-worker",
        "export-worker",
    }
    for name in serial_workers:
        worker = services[name]
        assert worker["environment"]["ITIME_DB_POOL_SIZE"] == "1"
        assert worker["environment"]["ITIME_DB_MAX_OVERFLOW"] == "0"
    for name in ("subscription-worker", "public-link-worker"):
        assert (
            services[name]["environment"]["ITIME_PERF_FETCH_DELAY_SECONDS"]
            == "0.25"
        )

    application_connections = 2 * (4 + 1) + 2 * 1 + len(serial_workers)
    helper_connections = 4
    assert application_connections + helper_connections == 20
    assert application_connections + helper_connections <= 50 * 0.8


def test_performance_compose_stays_inside_memory_and_network_boundaries():
    config = load_compose_config()
    services = config["services"]
    assert isinstance(services, dict)

    steady_services = {
        "postgres",
        "api",
        "ai-worker",
        "mail-worker",
        "subscription-worker",
        "public-link-worker",
        "export-worker",
    }
    memory_bytes = 0
    for name in steady_services:
        service = services[name]
        replicas = (service.get("deploy") or {}).get("replicas", 1)
        memory_bytes += int(service["mem_limit"]) * int(replicas)
    assert memory_bytes <= 4 * 1024**3
    assert int(services["postgres"]["mem_limit"]) == 1024 * 1024**2
    assert int(services["api"]["mem_limit"]) == 1280 * 1024**2

    for name, service in services.items():
        assert service["restart"] == "no", name
        assert not service.get("privileged", False), name
    for name in ("postgres", "api"):
        published = services[name]["ports"]
        assert len(published) == 1
        assert published[0]["host_ip"] == "127.0.0.1"
        assert "published" not in published[0]
