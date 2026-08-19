from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from urllib.parse import urlparse

from flask import Flask, g
from dotenv import load_dotenv

from .capacity import DemoCapacityStore
from .cookie_availability import install_cookie_availability
from .gift_card_recovery import install_gift_card_recovery
from .hosted_checkout_handoff import install_hosted_checkout_handoff
from .menu import StaticMenuProvider
from .operations import configure_structured_logging
from .routes import storefront
from .scheduling import parse_pickup_schedule
from .square import (
    SQUARE_API_VERSION,
    SquareCatalogProvider,
    SquareClient,
    SquareCommerce,
)


APP_VERSION = "0.18.34"
SHARED_ASSET_VERSION = "0.18.32"


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


def _boolean_setting(
    environment: Mapping[str, str], name: str, default: str
) -> bool:
    value = environment.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be either true or false.")
    return value == "true"


def create_app(test_config: dict | None = None) -> Flask:
    testing = bool(test_config and test_config.get("TESTING"))
    running_under_pytest = "PYTEST_CURRENT_TEST" in os.environ
    if not testing and not running_under_pytest:
        load_dotenv()

    # Tests must be reproducible on machines that have a real Square .env.
    # Integration tests opt into Square explicitly through test_config.
    environment: Mapping[str, str] = {} if testing else os.environ

    demo_mode = environment.get("DEMO_MODE", "true").lower() == "true"
    configured_secret = environment.get("SECRET_KEY", "").strip()
    square_application_id = environment.get("SQUARE_APPLICATION_ID", "").strip()

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
        PICKUP_SCHEDULE=environment.get("PICKUP_SCHEDULE", "").strip(),
        CART_TOTAL_LIMIT=_positive_integer(
            environment.get("CART_TOTAL_LIMIT", "8"), "CART_TOTAL_LIMIT"
        ),
        ORDERING_ENABLED=_boolean_setting(
            environment, "ORDERING_ENABLED", "true"
        ),
        FALLBACK_ORDERING_URL=environment.get(
            "FALLBACK_ORDERING_URL", ""
        ).strip(),
        SALES_TAX_RATE=0.08,
        APP_VERSION=APP_VERSION,
        SERVICE_HOURS={
            3: ("16:00", "20:00"),  # Thursday
            4: ("16:00", "20:00"),  # Friday
            5: ("11:00", "20:00"),  # Saturday
            6: ("11:00", "16:00"),  # Sunday
        },
        SQUARE_ENVIRONMENT=environment.get("SQUARE_ENVIRONMENT", "sandbox").lower(),
        PUBLIC_BASE_URL=environment.get("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        SQUARE_CATALOG_ENABLED=environment.get(
            "SQUARE_CATALOG_ENABLED", "false"
        ).lower()
        == "true",
        SQUARE_LOCATION_ID=environment.get("SQUARE_LOCATION_ID", "").strip(),
        SQUARE_ACCESS_TOKEN=environment.get("SQUARE_ACCESS_TOKEN", "").strip(),
        SQUARE_APPLICATION_ID=square_application_id,
        SQUARE_GIFT_CARDS_ENABLED=bool(square_application_id),
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
    for boolean_name in ("ORDERING_ENABLED",):
        if not isinstance(app.config[boolean_name], bool):
            raise RuntimeError(f"{boolean_name} must be either true or false.")
    app.config["PICKUP_SCHEDULE"] = parse_pickup_schedule(
        app.config.get("PICKUP_SCHEDULE", ""),
        app.config["SLOT_INTERVAL_MINUTES"],
    )
    configured_capacities = [
        window[2]
        for windows in app.config["PICKUP_SCHEDULE"].values()
        for window in windows
    ]
    app.config["PIZZA_MAX_SLOT_CAPACITY"] = max(
        [app.config["PIZZA_SLOT_CAPACITY"], *configured_capacities]
    )
    if app.config["PIZZA_CART_LIMIT"] > app.config["PIZZA_MAX_SLOT_CAPACITY"]:
        raise RuntimeError(
            "PIZZA_CART_LIMIT cannot exceed the largest configured pickup-slot capacity."
        )
    if app.config["PIZZA_CART_LIMIT"] > app.config["CART_TOTAL_LIMIT"]:
        raise RuntimeError("PIZZA_CART_LIMIT cannot exceed CART_TOTAL_LIMIT.")
    if app.config.get("SQUARE_GIFT_CARDS_ENABLED") and not str(
        app.config.get("SQUARE_APPLICATION_ID", "")
    ).strip():
        raise RuntimeError(
            "SQUARE_APPLICATION_ID must be set when Square gift cards are enabled."
        )
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

    if not app.config["DEMO_MODE"]:
        public_url = urlparse(app.config["PUBLIC_BASE_URL"])
        if (
            public_url.scheme not in {"http", "https"}
            or not public_url.netloc
            or public_url.path not in {"", "/"}
            or public_url.params
            or public_url.query
            or public_url.fragment
        ):
            raise RuntimeError(
                "PUBLIC_BASE_URL must be the public origin of this site, such as https://order.example.com."
            )
        if (
            app.config["SQUARE_ENVIRONMENT"] == "production"
            and public_url.scheme != "https"
        ):
            raise RuntimeError(
                "PUBLIC_BASE_URL must use https in Square production mode."
            )
        if (
            app.config.get("SQUARE_GIFT_CARDS_ENABLED")
            and public_url.scheme != "https"
        ):
            raise RuntimeError(
                "PUBLIC_BASE_URL must use https when Square gift cards are enabled."
            )

    fallback_url = app.config.get("FALLBACK_ORDERING_URL", "")
    if fallback_url:
        parsed_fallback = urlparse(fallback_url)
        if (
            parsed_fallback.scheme not in {"http", "https"}
            or not parsed_fallback.netloc
            or parsed_fallback.username
            or parsed_fallback.password
        ):
            raise RuntimeError(
                "FALLBACK_ORDERING_URL must be a complete http or https URL."
            )
        if (
            not app.config["DEMO_MODE"]
            and app.config["SQUARE_ENVIRONMENT"] == "production"
            and parsed_fallback.scheme != "https"
        ):
            raise RuntimeError(
                "FALLBACK_ORDERING_URL must use https in Square production mode."
            )

    configure_structured_logging(app)
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
    else:
        required = (
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
            logger=app.logger,
        )
        commerce = SquareCommerce(
            client=client,
            location_id=app.config["SQUARE_LOCATION_ID"],
            timezone_name=app.config["TIMEZONE"],
        )
        app.extensions["square_commerce"] = commerce

    app.register_blueprint(storefront)

    @app.before_request
    def create_csp_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(18)

    install_gift_card_recovery(app)
    install_cookie_availability(app)
    install_hosted_checkout_handoff(app)

    @app.context_processor
    def inject_csp_nonce() -> dict:
        return {
            "csp_nonce": g.get("csp_nonce", ""),
            "asset_version": SHARED_ASSET_VERSION,
            "storefront_asset_version": app.config["APP_VERSION"],
            "gift_card_asset_version": app.config["APP_VERSION"],
            "ordering_enabled": app.config["ORDERING_ENABLED"],
            "fallback_ordering_url": app.config["FALLBACK_ORDERING_URL"],
        }

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not app.config["DEMO_MODE"]:
            nonce = g.get("csp_nonce", "")
            gift_cards_enabled = bool(
                app.config.get("SQUARE_GIFT_CARDS_ENABLED")
            )
            square_web_origin = (
                "https://sandbox.web.squarecdn.com"
                if app.config["SQUARE_ENVIRONMENT"] == "sandbox"
                else "https://web.squarecdn.com"
            )
            square_pci_origin = (
                "https://pci-connect.squareupsandbox.com"
                if app.config["SQUARE_ENVIRONMENT"] == "sandbox"
                else "https://pci-connect.squareup.com"
            )
            script_sources = f"script-src 'self' 'nonce-{nonce}'"
            connect_sources = "connect-src 'self'"
            style_sources = "style-src 'self' 'unsafe-inline'"
            font_sources = "font-src 'self'"
            frame_sources = "frame-src 'none'"
            if gift_cards_enabled:
                script_sources += f" {square_web_origin}"
                connect_sources += (
                    f" {square_web_origin} {square_pci_origin}"
                    " https://o160250.ingest.sentry.io"
                )
                style_sources += f" {square_web_origin}"
                font_sources += (
                    " https://square-fonts-production-f.squarecdn.com"
                    " https://d1g145x70srn7h.cloudfront.net"
                )
                frame_sources = f"frame-src 'self' {square_web_origin}"
            response.headers["Content-Security-Policy"] = "; ".join(
                (
                    "default-src 'self'",
                    script_sources,
                    connect_sources,
                    style_sources,
                    font_sources,
                    frame_sources,
                    "img-src 'self' data: https:",
                    "object-src 'none'",
                    "base-uri 'self'",
                    "form-action 'self'",
                    "frame-ancestors 'none'",
                )
            )
        return response

    return app
