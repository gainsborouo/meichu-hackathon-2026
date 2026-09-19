from pathlib import Path


def test_dev_backend_mounts_migrations() -> None:
    compose = Path(__file__).resolve().parents[2] / "compose.dev.yaml"
    assert "./backend/alembic:/app/alembic:ro" in compose.read_text(encoding="utf-8")
