from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get(f"{get_settings().api_v1_prefix}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
