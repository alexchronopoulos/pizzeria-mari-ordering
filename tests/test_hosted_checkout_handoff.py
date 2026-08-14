from flask import Blueprint, Flask, redirect

from app.hosted_checkout_handoff import install_hosted_checkout_handoff


def handoff_app() -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    storefront = Blueprint("storefront", __name__)

    @storefront.post("/checkout")
    def checkout():
        return redirect(
            "/checkout/square?attempt=attempt-123", code=303
        )

    @storefront.get("/checkout/square")
    def hosted_checkout():
        return "Opening Square"

    app.register_blueprint(storefront)
    install_hosted_checkout_handoff(app)
    return app


def test_checkout_uses_mobile_safe_intermediate_page():
    client = handoff_app().test_client()

    started = client.post("/checkout")
    assert started.status_code == 303
    assert started.headers["Location"] == (
        "/checkout/square?attempt=attempt-123"
    )

    handoff = client.get(started.headers["Location"])
    assert handoff.status_code == 200
    assert handoff.get_data(as_text=True) == "Opening Square"
