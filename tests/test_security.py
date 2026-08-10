from __future__ import annotations

import httpx
import pytest

from app import create_app


def test_testing_mode_is_isolated_from_real_square_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("SQUARE_CATALOG_ENABLED", "true")
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    monkeypatch.setenv("SQUARE_LOCATION_ID", "production-location-id")
    monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "production-access-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://production.example.com")

    app = create_app({"TESTING": True, "SECRET_KEY": "test"})

    assert app.config["DEMO_MODE"] is True
    assert app.config["USE_SQUARE_DATA"] is False
    assert app.config["SQUARE_ENVIRONMENT"] == "sandbox"
    assert app.config["PUBLIC_BASE_URL"] == ""


def test_live_checkout_requires_a_valid_public_base_url() -> None:
    config = {
        "TESTING": True,
        "DEMO_MODE": False,
        "SECRET_KEY": "test-secret-not-for-production",
        "SQUARE_LOCATION_ID": "LOCATION",
        "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
        "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(
            lambda request: httpx.Response(500)
        ),
    }
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL must be"):
        create_app(config)

    with pytest.raises(RuntimeError, match="must use https"):
        create_app(
            {
                **config,
                "SQUARE_ENVIRONMENT": "production",
                "PUBLIC_BASE_URL": "http://orders.example.test",
            }
        )


def test_gift_cards_require_a_public_application_id_and_https() -> None:
    base = {
        "TESTING": True,
        "DEMO_MODE": False,
        "SECRET_KEY": "test-secret-not-for-production",
        "SQUARE_LOCATION_ID": "LOCATION",
        "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
        "SQUARE_GIFT_CARDS_ENABLED": True,
    }
    with pytest.raises(RuntimeError, match="SQUARE_APPLICATION_ID must be set"):
        create_app({**base, "PUBLIC_BASE_URL": "https://orders.example.test"})

    with pytest.raises(RuntimeError, match="must use https when Square gift cards"):
        create_app(
            {
                **base,
                "SQUARE_APPLICATION_ID": "sandbox-sq0idb-public-test-id",
                "PUBLIC_BASE_URL": "http://orders.example.test",
            }
        )


def test_non_demo_mode_requires_a_private_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SECRET_KEY=a-real-looking-local-secret-that-tests-must-ignore\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app()


def test_non_demo_mode_rejects_known_placeholder_secret(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("SECRET_KEY", "replace-me")

    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app()
