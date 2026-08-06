from __future__ import annotations

import os
import secrets
from collections.abc import Mapping

from flask import Flask, g
from dotenv import load_dotenv

from .capacity import DemoCapacityStore
from .menu import StaticMenuProvider
from .routes import storefront
from .square import (
    CheckoutLockManager,
    SQUARE_API_VERSION,
    SQUARE_JS_URLS,
    SquareCatalogProvider,
    SquareClient,
    SquareCommerce,
)


APP_VERSION = "0.12.0"


def _csv_setting(
    environment: Mapping[str, str],
    name: str,
    default: str,
) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in environment.get(name, default).split(",")
        if value.strip()
    )


def _unique_values(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Combine ordered settings without publishing a category twice."""
    return tuple(dict.fromkeys(value for group in groups for value in group))


def _positive_integer(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive whole number.") from exc
    if parsed < 1 or isinstance(value, float) and not value.is_integer():
        raise RuntimeError(f"{name} must be a positive whole number.")
    return parsed


def create_app(test_config: dict | None = None) -> Flask:
    testing = bool(test_config and test_config.get("TESTING"))
    if not testing:
        load_dotenv()

    # Tests must be reproducible on machines that have a real Square .env.
    # Integration tests opt into Square explicitly through test_config.
    environment: Mapping[str, str] = {} if testing else os.environ

    demo_mode = environment.get("DEMO_MODE", "true").lower() == "true"
    configured_secret = environment.get("SECRET_KEY", "").strip()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=(
            configured_secret
            or (secrets.token_urlsafe(32) if demo_mode else "")
        ),
        DEMO_MODE=demo_mode,
        DEBUG=(
            demo_mode
            and environment.get("FLASK_DEBUG", "false").lower() == "true"
        ),
        MAX_CONTENT_LENGTH=32 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TIMEZONE="America/New_York",
        ADVANCE_DAYS=7,
        SLOT_INTERVAL_MINUTES=15,
        PIZZA_CART_LIMIT=_positive_integer(
            environment.get("PIZZA_CART_LIMIT", "3"), "PIZZA_CART_LIMIT"
        ),
        PIZZA_SLOT_CAPACITY=_positive_integer(
            environment.get("PIZZA_SLOT_CAPACITY", "3"), "PIZZA_SLOT_CAPACITY"
        ),
        CART_TOTAL_LIMIT=_positive_integer(
            environment.get("CART_TOTAL_LIMIT", "8"), "CART_TOTAL_LIMIT"
        ),
        SALES_TAX_RATE=0.08,
        APP_VERSION=APP_VERSION,
        SERVICE_HOURS={
            3: ("16:00", "20:00"),  # Thursday
            4: ("16:00", "20:00"),  # Friday
            5: ("11:00", "20:00"),  # Saturday
            6: ("11:00", "16:00"),  # Sunday
        },
        SQUARE_ENVIRONMENT=environment.get("SQUARE_ENVIRONMENT", "sandbox").lower(),
        SQUARE_CATALOG_ENABLED=environment.get(
            "SQUARE_CATALOG_ENABLED", "false"
        ).lower()
        == "true",
        SQUARE_APPLICATION_ID=environment.get("SQUARE_APPLICATION_ID", "").strip(),
        SQUARE_LOCATION_ID=environment.get("SQUARE_LOCATION_ID", "").strip(),
        SQUARE_ACCESS_TOKEN=environment.get("SQUARE_ACCESS_TOKEN", "").strip(),
        SQUARE_API_VERSION=environment.get(
            "SQUARE_API_VERSION", SQUARE_API_VERSION
        ).strip(),
        SQUARE_ALLOWED_CATEGORY_NAMES=_csv_setting(
            environment,
            "SQUARE_ALLOWED_CATEGORY_NAMES",
            "Seasonal Special Pies,Traditional Pies,Mari Pies",
        ),
        SQUARE_ADDITIONAL_CATEGORY_NAMES=_csv_setting(
            environment,
            "SQUARE_ADDITIONAL_CATEGORY_NAMES",
            "Sides,Desserts,Salads,Drinks",
        ),
        SQUARE_PIZZA_CATEGORY_NAMES=_csv_setting(
            environment,
            "SQUARE_PIZZA_CATEGORY_NAMES",
            "Seasonal Special Pies,Traditional Pies,Mari Pies",
        ),
        SQUARE_EXCLUDED_MODIFIER_LIST_NAMES=_csv_setting(
            environment,
            "SQUARE_EXCLUDED_MODIFIER_LIST_NAMES",
            "Sides & Desserts,Drinks",
        ),
        SQUARE_CATALOG_CACHE_SECONDS=int(
            environment.get("SQUARE_CATALOG_CACHE_SECONDS", "30")
        ),
    )

    if test_config:
        app.config.update(test_config)

    for threshold_name in (
        "PIZZA_CART_LIMIT",
        "PIZZA_SLOT_CAPACITY",
        "CART_TOTAL_LIMIT",
    ):
        app.config[threshold_name] = _positive_integer(
            app.config[threshold_name], threshold_name
        )
    if app.config["PIZZA_CART_LIMIT"] > app.config["PIZZA_SLOT_CAPACITY"]:
        raise RuntimeError(
            "PIZZA_CART_LIMIT cannot exceed PIZZA_SLOT_CAPACITY."
        )
    if app.config["PIZZA_CART_LIMIT"] > app.config["CART_TOTAL_LIMIT"]:
        raise RuntimeError("PIZZA_CART_LIMIT cannot exceed CART_TOTAL_LIMIT.")
    app.config["CATEGORY_LIMITS"] = {
        "pizza": app.config["PIZZA_CART_LIMIT"]
    }

    insecure_placeholders = {"replace-me", "development-only-change-me"}
    if not app.config["DEMO_MODE"] and (
        not app.config["SECRET_KEY"]
        or app.config["SECRET_KEY"] in insecure_placeholders
        or (
            not app.config.get("TESTING")
            and len(app.config["SECRET_KEY"]) < 32
        )
    ):
        raise RuntimeError(
            "SECRET_KEY must be set to a private value of at least 32 characters when DEMO_MODE is false."
        )

    app.extensions["checkout_locks"] = CheckoutLockManager()
    square_data_enabled = bool(
        app.config["SQUARE_CATALOG_ENABLED"] or not app.config["DEMO_MODE"]
    )
    app.config["USE_SQUARE_DATA"] = square_data_enabled
    app.config["SESSION_COOKIE_SECURE"] = bool(
        not app.config["DEMO_MODE"]
        and app.config["SQUARE_ENVIRONMENT"] == "production"
    )
    app.extensions["capacity_store"] = DemoCapacityStore()
    if not square_data_enabled:
        app.extensions["menu_provider"] = StaticMenuProvider()
        app.extensions["square_commerce"] = None
        app.config["SQUARE_JS_URL"] = None
    else:
        required = (
            "SQUARE_APPLICATION_ID",
            "SQUARE_LOCATION_ID",
            "SQUARE_ACCESS_TOKEN",
        )
        missing = [name for name in required if not app.config[name]]
        if missing:
            raise RuntimeError(
                "Square mode requires these private .env settings: "
                + ", ".join(missing)
            )
        client = SquareClient(
            access_token=app.config["SQUARE_ACCESS_TOKEN"],
            environment=app.config["SQUARE_ENVIRONMENT"],
            api_version=app.config["SQUARE_API_VERSION"],
            transport=app.config.get("SQUARE_HTTP_TRANSPORT"),
        )
        app.extensions["menu_provider"] = SquareCatalogProvider(
            client=client,
            location_id=app.config["SQUARE_LOCATION_ID"],
            allowed_category_names=_unique_values(
                tuple(app.config["SQUARE_ALLOWED_CATEGORY_NAMES"]),
                tuple(app.config["SQUARE_ADDITIONAL_CATEGORY_NAMES"]),
            ),
            pizza_category_names=tuple(app.config["SQUARE_PIZZA_CATEGORY_NAMES"]),
            excluded_modifier_list_names=tuple(
                app.config["SQUARE_EXCLUDED_MODIFIER_LIST_NAMES"]
            ),
            cache_seconds=app.config["SQUARE_CATALOG_CACHE_SECONDS"],
        )
        app.extensions["square_commerce"] = SquareCommerce(
            client=client,
            location_id=app.config["SQUARE_LOCATION_ID"],
            timezone_name=app.config["TIMEZONE"],
        )
        app.config["SQUARE_JS_URL"] = (
            None
            if app.config["DEMO_MODE"]
            else SQUARE_JS_URLS[app.config["SQUARE_ENVIRONMENT"]]
        )

    app.register_blueprint(storefront)

    @app.before_request
    def create_csp_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(18)

    @app.context_processor
    def inject_csp_nonce() -> dict:
        return {
            "csp_nonce": g.get("csp_nonce", ""),
            "asset_version": app.config["APP_VERSION"],
        }

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not app.config["DEMO_MODE"]:
            nonce = g.get("csp_nonce", "")
            sandbox = app.config["SQUARE_ENVIRONMENT"] == "sandbox"
            sdk_origin = (
                "https://sandbox.web.squarecdn.com"
                if sandbox
                else "https://web.squarecdn.com"
            )
            pci_origin = (
                "https://pci-connect.squareupsandbox.com"
                if sandbox
                else "https://pci-connect.squareup.com"
            )
            response.headers["Content-Security-Policy"] = "; ".join(
                (
                    "default-src 'self'",
                    f"script-src 'self' 'nonce-{nonce}' {sdk_origin}",
                    f"frame-src 'self' {sdk_origin}",
                    f"connect-src 'self' {sdk_origin} {pci_origin} https://o160250.ingest.sentry.io",
                    f"style-src 'self' 'unsafe-inline' {sdk_origin}",
                    "font-src 'self' https://square-fonts-production-f.squarecdn.com https://d1g145x70srn7h.cloudfront.net",
                    "img-src 'self' data: https:",
                    "object-src 'none'",
                    "base-uri 'self'",
                    "form-action 'self'",
                    "frame-ancestors 'none'",
                )
            )
        return response

    return app
