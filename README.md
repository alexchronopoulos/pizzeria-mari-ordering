# Pizzeria Mari Ordering

A simple Flask ordering portal that uses Square as its business-data system of record while enforcing Pizzeria Mari's cart and pickup-slot rules.

## Current v0.7 capabilities

- Orders through seven days in advance with configured 15-minute pickup times.
- Three-pizza cart and slot limits, plus a configurable eight-item overall limit.
- Full pickup times remain visible and clearly labeled instead of disappearing.
- Compagnon display type, Semplicita body type, Pizzeria Mari colors, and responsive layouts.
- Quantity controls on the menu and checkout, with preventative limit feedback.
- Square Catalog categories, items, variations, descriptions, prices, images, sold-out state, and modifier lists.
- One compact addition picker assembled from Square's Whole Pie Additions, First Half Pie Additions, and Second Half Pie Additions lists.
- Every other modifier list attached to an item is rendered and validated automatically.
- Square-calculated taxes and automatic catalog discounts.
- Square Web Payments SDK card tokenization; raw card details never reach Flask.
- Scheduled Square pickup orders with the buyer, pickup time, notes, catalog-backed items, and catalog-backed modifiers.
- Square Payments API charges with a 15% default tip, custom tips, order ID, buyer email, and idempotency keys.
- Square scheduled orders are queried to calculate slot usage; no catalog, customer, payment, or order database exists in the app.
- The cart and pickup choice live only in Flask's signed browser-session cookie.

The local demo uses temporary in-memory slot counters that disappear on restart. Live Square mode does not use them.

## Safe first Square connection

Python 3.12 or newer and `uv` are recommended.

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
```

Create an application in the [Square Developer Console](https://developer.squareup.com/apps), then copy its Application ID, Access Token, and Location ID into the ignored `.env` file. Never paste the access token into source code, Git, chat, browser JavaScript, or a public issue.

To inspect a real Square catalog without creating orders or charging cards, use:

```dotenv
DEMO_MODE=true
SQUARE_CATALOG_ENABLED=true
SQUARE_ENVIRONMENT=production
SQUARE_APPLICATION_ID=your-production-application-id
SQUARE_LOCATION_ID=your-production-location-id
SQUARE_ACCESS_TOKEN=your-production-access-token
```

Then start the site:

```bash
uv run python run.py
```

This read-only/demo combination pulls the real menu, modifiers, Square pricing, and existing scheduled-order capacity while leaving checkout simulated. It is the recommended first connection.

## Square Sandbox checkout

Square Sandbox has a separate catalog from the production Square account. The configured categories and items must exist in the Sandbox test account before its catalog IDs can be used in Sandbox orders.

Set the Developer Console to Sandbox, copy the Sandbox credentials into `.env`, generate a private Flask secret, and use:

```dotenv
DEMO_MODE=false
SECRET_KEY=generate-a-long-random-value
SQUARE_CATALOG_ENABLED=true
SQUARE_ENVIRONMENT=sandbox
SQUARE_APPLICATION_ID=your-sandbox-application-id
SQUARE_LOCATION_ID=your-sandbox-location-id
SQUARE_ACCESS_TOKEN=your-sandbox-access-token
```

Generate the secret locally with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Square's [Sandbox test cards](https://developer.squareup.com/docs/devtools/sandbox/payments) can then create paid scheduled orders in the Sandbox account. The Web Payments SDK script and backend API host change automatically with `SQUARE_ENVIRONMENT`.

Do not set `SQUARE_ENVIRONMENT=production` with `DEMO_MODE=false` until the controlled-launch checklist is complete. Production mode charges real cards.

## Category and menu configuration

These `.env` values select exact Square category names and their display order:

```dotenv
SQUARE_ALLOWED_CATEGORY_NAMES=Seasonal Special Pies,Traditional Pies,Mari Pies
SQUARE_PIZZA_CATEGORY_NAMES=Seasonal Special Pies,Traditional Pies,Mari Pies
```

All items in `SQUARE_PIZZA_CATEGORY_NAMES` consume pizza cart and pickup-slot capacity. All other allowed categories appear on the menu and count only toward the overall cart limit.

Catalog results are held in process memory for 30 seconds by default to keep page loads fast. They are never written to disk:

```dotenv
SQUARE_CATALOG_CACHE_SECONDS=30
```

The app pins Square API version `2026-07-15` in every request. Upgrade it deliberately after reviewing Square's release notes.

## What remains before production

- A short-lived DynamoDB lease so multiple redundant Lambda instances serialize checkout for the same pickup slot. It will contain only a slot key, random lease owner, and expiry—not menu, cart, customer, payment, or order data.
- Webhook reconciliation for the rare case where a Square payment succeeds but the browser or network disappears before confirmation.
- Confirmation email delivery. Square returns a receipt URL but the Payments API does not provide a general “email this receipt” action, so the app will send the Square receipt link through SES without retaining customer data.
- Coupon and gift-card redemption. The field remains visible but clearly reports that redemption is not connected yet.
- AWS deployment, monitoring, and a controlled production launch.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the transaction boundary and deployment plan.

## Public Git repository safety

Run this once from the project directory:

```bash
bash setup-git.sh
```

It initializes the `master` branch, audits the public file set, installs the pre-commit credential check, and creates the first commit when Git identity is configured. GitHub Actions repeats the audit and all tests on pushes and pull requests.

The repository excludes `.env`, databases, virtual environments, caches, logs, keys, downloaded ZIP archives, and licensed font binaries. The public source contains only empty Square placeholders. Keep local font files in `app/static/fonts/`; their expected names are documented in that directory.

## References

- [Square Web Payments SDK quickstart](https://developer.squareup.com/docs/web-payments/quickstart/add-sdk-to-web-client)
- [Square Catalog API](https://developer.squareup.com/docs/catalog-api/what-it-does)
- [Create Square orders](https://developer.squareup.com/docs/orders-api/create-orders)
- [Pay for Square orders](https://developer.squareup.com/docs/orders-api/pay-for-orders)
- [Square Web Payments content security policy](https://developer.squareup.com/docs/web-payments/content-security-policy)
