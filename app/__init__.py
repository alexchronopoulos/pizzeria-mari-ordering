from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv

from .capacity import SQLiteCapacityStore
from .routes import storefront


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()

    demo_mode = os.environ.get("DEMO_MODE", "true").lower() == "true"
    configured_secret = os.environ.get("SECRET_KEY", "").strip()
    insecure_placeholders = {"replace-me", "development-only-change-me"}
    if not demo_mode and (
        not configured_secret or configured_secret in insecure_placeholders
    ):
        raise RuntimeError(
            "SECRET_KEY must be set to a strong, private value when DEMO_MODE is false."
        )

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=configured_secret or secrets.token_urlsafe(32),
        DATABASE=str(Path(app.instance_path) / "ordering.sqlite3"),
        DEMO_MODE=demo_mode,
        DEBUG=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        TIMEZONE="America/New_York",
        ADVANCE_DAYS=7,
        SLOT_INTERVAL_MINUTES=15,
        PIZZA_SLOT_CAPACITY=3,
        CART_TOTAL_LIMIT=8,
        CATEGORY_LIMITS={"pizza": 3},
        SALES_TAX_RATE=0.08,
        SERVICE_HOURS={
            3: ("16:00", "20:00"),  # Thursday
            4: ("16:00", "20:00"),  # Friday
            5: ("11:00", "20:00"),  # Saturday
            6: ("11:00", "16:00"),  # Sunday
        },
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    capacity_store = SQLiteCapacityStore(app.config["DATABASE"])
    capacity_store.initialize()
    app.extensions["capacity_store"] = capacity_store

    app.register_blueprint(storefront)
    return app
