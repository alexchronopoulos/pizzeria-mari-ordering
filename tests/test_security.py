from __future__ import annotations

import pytest

from app import create_app


def test_testing_mode_is_isolated_from_real_square_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("SQUARE_CATALOG_ENABLED", "true")
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    monkeypatch.setenv("SQUARE_APPLICATION_ID", "production-app-id")
    monkeypatch.setenv("SQUARE_LOCATION_ID", "production-location-id")
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "production-access-token")

    app = create_app({"TESTING": True, "SECRET_KEY": "test"})

    assert app.config["DEMO_MODE"] is True
    assert app.config["USE_SQUARE_DATA"] is False
    assert app.config["SQUARE_ENVIRONMENT"] == "sandbox"
    assert app.config["SQUARE_JS_URL"] is None


def test_non_demo_mode_requires_a_private_secret(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app()


def test_non_demo_mode_rejects_known_placeholder_secret(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("SECRET_KEY", "replace-me")

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app()
