from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlparse

from flask import Flask, redirect, request, session


def _pending_checkout_url(app: Flask, attempt_id: str) -> str | None:
    pending = session.get("pending_square_checkout")
    if (
        not isinstance(pending, dict)
        or pending.get("mode") != "hosted"
        or not attempt_id
        or not secrets.compare_digest(
            str(pending.get("attempt_id", "")), attempt_id
        )
    ):
        return None

    checkout_url = str(pending.get("checkout_url", ""))
    parsed = urlparse(checkout_url)
    expected_host = (
        "sandbox.square.link"
        if app.config.get("SQUARE_ENVIRONMENT") == "sandbox"
        else "square.link"
    )
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        return None
    return checkout_url


def install_hosted_checkout_handoff(app: Flask) -> None:
    """Send a completed hosted-checkout setup straight to Square.

    The production shortcut removes an unnecessary second request to this app.
    Existing integration tests can retain the intermediate-page behavior unless
    they explicitly enable DIRECT_SQUARE_REDIRECT.
    """

    enabled = not app.config.get("TESTING") or app.config.get(
        "DIRECT_SQUARE_REDIRECT", False
    )
    if not enabled:
        return

    @app.before_request
    def resume_hosted_checkout_directly():
        if request.endpoint != "storefront.hosted_checkout":
            return None
        checkout_url = _pending_checkout_url(
            app, request.args.get("attempt", "")
        )
        return redirect(checkout_url, code=303) if checkout_url else None

    @app.after_request
    def start_hosted_checkout_directly(response):
        if (
            request.endpoint != "storefront.checkout"
            or request.method != "POST"
            or response.status_code not in {302, 303}
        ):
            return response

        location = response.headers.get("Location", "")
        parsed = urlparse(location)
        if parsed.path != "/checkout/square":
            return response
        attempt_id = parse_qs(parsed.query).get("attempt", [""])[0]
        checkout_url = _pending_checkout_url(app, attempt_id)
        if checkout_url:
            response.headers["Location"] = checkout_url
        return response
