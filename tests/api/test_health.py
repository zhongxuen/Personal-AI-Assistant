"""Zero-LLM sanity check: the API boots and can round-trip through the DB."""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_check_returns_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "jarvis-backend"


def test_health_check_accepts_head():
    # Keep-warm pingers may use HEAD; a 405 counts as a failed ping (see health.py).
    response = client.head("/api/health")
    assert response.status_code == 200


def test_health_reports_storage_mode(monkeypatch):
    from app.config.settings import Settings

    for env, expected in [
        (dict(storage_ephemeral=True), "ephemeral"),
        (dict(storage_ephemeral=False), "local-durable"),
    ]:
        settings = Settings(_env_file=None, **env)
        monkeypatch.setattr("app.api.routes.health.get_settings", lambda s=settings: s)
        assert client.get("/api/health").json()["storage_mode"] == expected


def test_storage_mode_auto_detects_render(monkeypatch):
    from app.config.settings import Settings

    monkeypatch.delenv("RENDER", raising=False)
    assert Settings(_env_file=None).storage_mode == "local-durable"
    monkeypatch.setenv("RENDER", "true")
    assert Settings(_env_file=None).storage_mode == "ephemeral"
    # An explicit setting always wins over detection.
    assert Settings(_env_file=None, storage_ephemeral=False).storage_mode == "local-durable"


def test_api_keys_are_stripped_of_pasted_whitespace():
    from app.config.settings import Settings

    settings = Settings(_env_file=None, openai_api_key=" sk-te\nst\n", gemini_api_key="\t\n")
    assert settings.openai_api_key == "sk-test"
    assert settings.gemini_api_key is None
