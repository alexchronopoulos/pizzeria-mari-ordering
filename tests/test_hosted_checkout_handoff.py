from flask import Blueprint, Flask, redirect, session

from app.hosted_checkout_handoff import install_hosted_checkout_handoff


def handoff_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test-secret",
        TESTING=True,
        DIRECT_SQUARE_REDIRECT=True,
        SQUARE_ENVIRONMENT="production",
    )
    storefront = Blueprint("storefront", __name__)

    @storefront.post("/checkout")
    def checkout():
        session["pending_square_checkout"] = {
            "attempt_id": "attempt-123",
            "mode": "hosted",
            "checkout_url": "https://square.link/u/secure-checkout",
        }
        return redirect(
            "/checkout/square?attempt=attempt-123", code=303
        )

    @storefront.get("/checkout/square")
    def hosted_checkout():
        return "intermediate page"

    app.register_blueprint(storefront)
    install_hosted_checkout_handoff(app)
    return app


def test_checkout_creation_redirects_straight_to_square():
    response = handoff_app().test_client().post("/checkout")

    assert response.status_code == 303
    assert response.headers["Location"] == (
        "https://square.link/u/secure-checkout"
    )


def test_existing_handoff_url_resumes_straight_to_square():
    client = handoff_app().test_client()
    with client.session_transaction() as browser_session:
        browser_session["pending_square_checkout"] = {
            "attempt_id": "attempt-123",
            "mode": "hosted",
            "checkout_url": "https://square.link/u/secure-checkout",
        }

    response = client.get("/checkout/square?attempt=attempt-123")

    assert response.status_code == 303
    assert response.headers["Location"] == (
        "https://square.link/u/secure-checkout"
    )


def test_handoff_rejects_an_untrusted_checkout_host():
    client = handoff_app().test_client()
    with client.session_transaction() as browser_session:
        browser_session["pending_square_checkout"] = {
            "attempt_id": "attempt-123",
            "mode": "hosted",
            "checkout_url": "https://example.com/not-square",
        }

    response = client.get("/checkout/square?attempt=attempt-123")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "intermediate page"
