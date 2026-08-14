# Pizzeria Mari Ordering

A simple Flask ordering portal that uses Square as its business-data system of record while enforcing Pizzeria Mari's cart and pickup-slot rules.

## Current v0.18.30 capabilities

- Orders through seven days in advance with configurable 15-minute pickup times.
- Recurring weekday and one-date pickup schedules with a separate pizza capacity for each time range.
- Configurable pizza-cart and overall-item limits.
- Every pickup time shows its remaining pizza capacity; full times remain visible and clearly labeled instead of disappearing.
- A saved pickup time is rechecked when the storefront or checkout page opens; if it has filled, the next available time is selected automatically.
- Pickup choices are included in the initial page response, so the picker opens immediately while a fresh availability check runs quietly.
- Cart additions and quantity changes reuse the menu and remaining capacity already shown to the customer instead of blocking on repeated Square reads.
- Compagnon display type, Semplicita body type, Pizzeria Mari colors, and responsive layouts.
- Quantity controls on the menu and checkout, with preventative limit feedback.
- Checkout collects required first name, last name, email, and phone fields with optional order notes.
- Buyers can opt to remember those four contact fields in their current browser for faster future checkout; the data is never stored by the server.
- Square Catalog categories, items, variations, descriptions, prices, images, sold-out state, and modifier lists.
- Square inventory-aware availability. Every menu item with a positive Square inventory count of one through four shows a visible `Low stock · 2 left` badge on its card and item dialog, even if Square omits the catalog tracking flag. An explicitly tracked zero count is unavailable, while five or more has no warning. The remaining quantity is enforced in the cart and rechecked before checkout.
- Large square menu photography with a pizza-centered focal crop and no image border; the complete item card has a border. Item details show a larger uncropped image above the item name.
- One compact Additions picker whose visible options come from Square's Whole Pie Additions list. Whole, first-half, and second-half choices resolve to the matching option and price in their respective Square lists.
- Every other customer-facing modifier list attached to an item is rendered and validated automatically, including Square's inherited/unlimited selection rules.
- Square's Sides, Desserts, Salads, and Drinks categories appear as regular main-menu sections while their old upsell modifier lists remain hidden.
- Square-calculated taxes and automatic catalog discounts.
- Square-hosted Checkout for cards and available digital wallets; raw payment details never reach Flask.
- Optional Square gift-card checkout through the Web Payments SDK. One gift card can cover the order or be combined with a credit/debit card for the remainder on the same Square order.
- Delayed capture for gift-card and remainder-card payments. Square captures both only after their authorized amounts equal the order total.
- Scheduled Square pickup orders with the buyer, pickup time, notes, catalog-backed items, and catalog-backed modifiers.
- Square-managed tips, eligible Square Marketing coupons, payment processing, and native emailed receipts.
- Only paid Square orders consume displayed pickup capacity. Unpaid hosted drafts and unfinished gift-card orders are ignored.
- Square scheduled orders are queried to calculate slot usage; no catalog, customer, payment, or order database exists in the app.
- The cart, pickup choice, and short-lived checkout handoff live only in Flask's signed browser-session cookie.
- Gift-card and card numbers are tokenized inside Square-hosted fields. Flask receives a short-lived single-use Square token and never stores or returns it.
- Production Gunicorn configuration for one worker with four threads.
- A lightweight `/health` endpoint for App Platform and external monitoring.
- An `ORDERING_ENABLED` emergency switch and configurable Square Online fallback link. New checkout creation pauses without interrupting payments already underway.
- Compact JSON application logs containing checkout-attempt, Square order, pickup, and state identifiers without customer or payment details.

The local demo uses temporary in-memory slot counters that disappear on restart. Live Square mode does not use them.

## Safe first Square connection

Python 3.12 or newer and `uv` are recommended.

```bash
cp .env.example .env
uv sync --extra dev
uv run pytest
```

Create an application in the [Square Developer Console](https://developer.squareup.com/apps), then copy its Access Token and Location ID into the ignored `.env` file. Never paste the access token into source code, Git, chat, browser JavaScript, or a public issue.

To inspect a real Square catalog without creating orders or charging cards, use:

```dotenv
DEMO_MODE=true
SQUARE_CATALOG_ENABLED=true
SQUARE_ENVIRONMENT=production
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
SQUARE_LOCATION_ID=your-sandbox-location-id
SQUARE_ACCESS_TOKEN=your-sandbox-access-token
# Public identifier from the same Sandbox application. Setting it enables gift cards.
SQUARE_APPLICATION_ID=your-sandbox-application-id
PUBLIC_BASE_URL=https://your-test-ordering-host.example
```

Generate the secret locally with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`PUBLIC_BASE_URL` is the exact origin the buyer's browser should return to after Square, with no path or trailing slash. Production requires HTTPS. Square's [Sandbox test cards](https://developer.squareup.com/docs/devtools/sandbox/payments) can then create paid scheduled orders in the Sandbox account.

The default payment choice creates a one-use Square payment link. Square shows tipping and eligible coupon controls, collects payment, emails its receipt, and redirects the buyer to this app for verified confirmation.

When `SQUARE_APPLICATION_ID` is set, checkout also offers **Pay with a Gift Card**. That path creates one scheduled Square order as a `DRAFT`, tokenizes the gift card directly with Square, opens the order only when payment is submitted, accepts a partial authorization when needed, and collects the remainder through Square's embedded card field. `PayOrder` then captures all approved payments together, so Square Dashboard shows one order with both tenders. Abandoning the gift-card page leaves only a draft, which the Production Dashboard already ignores.

Gift-card checkout intentionally does not offer online tipping or Marketing coupons. Payments API orders provide a Square receipt link on the confirmation page, but Square does not automatically email that receipt. Customers who want tips, coupons, digital wallets, or Square's emailed receipt can use the default hosted option.

Do not set `SQUARE_ENVIRONMENT=production` with `DEMO_MODE=false` until the controlled-launch checklist is complete. Production mode charges real cards.

## Category and menu configuration

These `.env` values select exact Square category names and their display order:

```dotenv
SQUARE_ALLOWED_CATEGORY_NAMES=Seasonal Special Pies,Traditional Pies,Mari Pies
SQUARE_ADDITIONAL_CATEGORY_NAMES=Sides,Desserts,Salads,Drinks
SQUARE_PIZZA_CATEGORY_NAMES=Seasonal Special Pies,Traditional Pies,Mari Pies
SQUARE_EXCLUDED_MODIFIER_LIST_NAMES=Sides & Desserts,Drinks
```

`SQUARE_ADDITIONAL_CATEGORY_NAMES` is appended to the allowed list, so an existing `.env` with the earlier pizza-only `SQUARE_ALLOWED_CATEGORY_NAMES` setting automatically publishes these sections. Duplicate names are removed while preserving display order.

All items in `SQUARE_PIZZA_CATEGORY_NAMES` consume pizza cart and pickup-slot capacity. Items in Sides, Desserts, Salads, and Drinks count only toward the overall cart limit.

`SQUARE_EXCLUDED_MODIFIER_LIST_NAMES` contains exact Square modifier-list names that should not appear inside an item's customization dialog. This does not delete or copy those lists; the catalog remains in Square.

Catalog and inventory results are joined and held in process memory for 30 seconds by default to keep page loads fast. They are never written to disk. Cart and checkout validation use the inventory count only when Square has enabled tracking for that variation at the configured location:

```dotenv
SQUARE_CATALOG_CACHE_SECONDS=30
```

Pickup availability is intentionally simple. The site reads scheduled orders from Square and counts paid `OPEN` and `COMPLETED` orders. One result supplies every date in the pickup picker, and a five-second in-process cache collapses duplicate reads made by closely spaced page and picker requests. Cart edits use the remaining capacity from the page the customer just reviewed. Unpaid hosted-checkout drafts and unfinished app-created gift-card orders are ignored, so an abandoned checkout cannot block a pickup time. There is no database, webhook, background cleanup, expiration job, or checkout-capacity recheck.

## Testing gift-card payments

Use the Sandbox Application ID, Location ID, and Access Token from the same Square application. The gift-card page must be served over HTTPS. Square documents `7783 3200 0000 0000` as its Web Payments SDK test gift-card number for a successful full gift-card payment.

For the split-payment test, create an active Sandbox gift card whose balance is lower than the order total, then enter that card and use Square's Sandbox Visa `4111 1111 1111 1111` for the remainder. Verify in Sandbox Square Dashboard that:

- there is one scheduled pickup order, not two;
- the gift-card and card payments both appear as tenders on that order;
- the order is `COMPLETED` only after the second payment;
- the pickup slot is counted only after the complete order is paid.

The split flow follows Square's delayed-capture sequence and should also receive one controlled Production test with a real Pizzeria Mari gift card before launch.

The application does not cancel an abandoned partial gift-card authorization. Square controls the eventual release of that delayed authorization. If a customer reports an interrupted split payment, inspect the order and payment in Square before asking them to try again.

## Pickup schedule and capacity

The default schedule remains Thursday and Friday 4–8 PM, Saturday 11 AM–8 PM,
and Sunday 11 AM–4 PM, with `PIZZA_SLOT_CAPACITY` pizzas at every pickup time.
Leave `PICKUP_SCHEDULE` empty to keep those defaults.

Set `PICKUP_SCHEDULE` to a JSON object to replace only the weekdays you want to
adjust. Each window includes its first pickup time, last pickup time, and pizza
capacity. End times are included. Times between windows are unavailable.

This example delays Sunday pickup until 2 PM and allows two pizzas per slot. It
also allows two pizzas early Thursday and three starting at 6 PM:

```dotenv
PICKUP_SCHEDULE='{"sunday":[{"start":"14:00","end":"17:00","pizzas":2}],"thursday":[{"start":"16:00","end":"17:45","pizzas":2},{"start":"18:00","end":"20:00","pizzas":3}]}'
```

In DigitalOcean, paste the JSON itself as the environment-variable value,
without the surrounding single quotes.

A `YYYY-MM-DD` key replaces the weekday schedule for that date only. This is
useful for a one-service capacity test:

```dotenv
PICKUP_SCHEDULE='{"2026-08-13":[{"start":"16:00","end":"17:45","pizzas":2},{"start":"18:00","end":"20:00","pizzas":3}]}'
```

Date rules take precedence over weekday rules. An empty list closes pickup for
that weekday or date. Weekdays and dates omitted from the JSON continue using
the built-in schedule. All times must align to the 15-minute interval, windows
cannot overlap, and configuration mistakes stop the app at startup with a clear
error instead of publishing an unintended schedule.

Cart and production thresholds are also configurable without editing source:

```dotenv
PIZZA_CART_LIMIT=3
PIZZA_SLOT_CAPACITY=3
CART_TOTAL_LIMIT=8
```

All three values must be positive whole numbers. `PIZZA_SLOT_CAPACITY` is the
fallback for days omitted from `PICKUP_SCHEDULE`. `PIZZA_CART_LIMIT` cannot
exceed the largest configured pickup-slot capacity or the total-item cart limit.

The app pins Square API version `2026-07-15` in every request. Upgrade it deliberately after reviewing Square's release notes.

## Production deployment

The initial production topology is deliberately one DigitalOcean App Platform instance with one Gunicorn worker and four threads. This is a simple, comfortably sized starting point for the expected traffic rather than a correctness requirement.

Use:

```bash
gunicorn --config gunicorn.conf.py run:app
```

Keep DNS in Route 53 and point a CNAME such as `order.pizzeriamari.com` to the target DigitalOcean displays. See [DEPLOYMENT.md](DEPLOYMENT.md) for the complete environment, custom-domain, monitoring, pause, and rollout setup.

The remaining launch work is operational testing rather than another application feature release:

- complete the full Sandbox matrix twice;
- complete the controlled Production card, gift-card, and split-payment transactions;
- verify every order in Square and the Production Dashboard;
- rehearse pause, fallback, and DigitalOcean rollback;
- soft-launch to trusted regulars before publishing the link.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the transaction boundary and deployment plan.

## Public Git repository safety

Run this once from the project directory:

```bash
bash setup-git.sh
```

It initializes the `master` branch, audits the public file set, installs the pre-commit credential check, and creates the first commit when Git identity is configured. GitHub Actions repeats the audit and all tests on pushes and pull requests.

The repository excludes `.env`, databases, virtual environments, caches, logs, keys, downloaded ZIP archives, and unverified font binaries. The three storefront fonts in `app/static/fonts/` are explicitly allowlisted because their redistribution licenses have been verified. Their attribution and full license terms are included in `LICENSES/`. The public source contains only empty Square placeholders.

## References

- [Square Checkout API](https://developer.squareup.com/docs/checkout-api)
- [Square order checkout](https://developer.squareup.com/docs/checkout-api/square-order-checkout)
- [Optional hosted-checkout configuration](https://developer.squareup.com/docs/checkout-api/optional-checkout-configurations)
- [Square gift-card payments](https://developer.squareup.com/docs/web-payments/gift-cards-intro)
- [Partial gift-card walkthrough](https://developer.squareup.com/docs/web-payments/gift-card-walkthrough)
- [Split online payments](https://developer.squareup.com/docs/commerce/scenarios/split-online-payment)
- [DigitalOcean App Platform custom domains](https://docs.digitalocean.com/products/app-platform/how-to/manage-domains/)
- [Square Catalog API](https://developer.squareup.com/docs/catalog-api/what-it-does)
