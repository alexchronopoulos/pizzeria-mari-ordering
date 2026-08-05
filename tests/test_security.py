from __future__ import annotations

import pytest

from app import create_app


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
