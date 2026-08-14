from __future__ import annotations

from flask import Flask


def install_hosted_checkout_handoff(app: Flask) -> None:
    """Keep the mobile-safe intermediate Square handoff page.

    The storefront's existing /checkout/square route renders a short page that
    navigates to Square from a normal GET. Directly redirecting the checkout
    form's cross-site POST response is unreliable in mobile browsers, so this
    extension intentionally installs no redirect hooks.
    """

    return None
