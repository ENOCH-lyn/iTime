import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


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
