# Pizzeria Mari Ordering

An intentionally simple Flask ordering portal with category-aware cart limits and true per-slot pizza capacity.

This first iteration is a local prototype. It reproduces the important parts of the existing Square storefront, makes the pickup date and time much more prominent, and implements the capacity rule that Square Online cannot express.

## What works now

- Server-rendered Flask storefront with responsive desktop and mobile layouts.
- Only the configured menu categories are shown.
- Current Pizzeria Mari hours and 15-minute pickup slots.
- Ordering from today through seven days in advance.
- The selected pickup day and time remain visible on both the menu and checkout.
- Pickup time can be changed in place from checkout without returning to the menu.
- Configurable cart-wide and capacity-category limits.
- A default limit of three pizzas per cart.
- Quantity controls on both the menu cart and checkout order summary.
- A clear three-pizza maximum message on the Add to Order button when a customer tries to exceed the limit.
- A default limit of three confirmed pizzas per pickup slot.
- Slots remain open internally until all three pizza positions are claimed; customers see only the pickup times that can accommodate their cart, never the production count.
- Server-side capacity enforcement inside a SQLite write transaction, preventing two local checkouts from both claiming the final capacity.
- Branded logo with Compagnon for display headings and Semplicita for body copy.
- One compact additions picker with whole-pie, first-half, and second-half placement instead of three repetitive modifier lists.
- Server-validated modifier selections and modifier pricing.
- Guest checkout fields for name, email, phone, coupon/gift card code, tip, and order notes.
- An 8% configurable Rensselaer County sales-tax calculation, a preselected 15% tip, and a custom tip amount.
- A clearly labeled demo checkout and confirmation screen.

## What is intentionally simulated

The prototype does not charge a card, validate coupon or gift card codes, write an order to Square, send an email, or deploy to AWS. Those actions require credentials and should only be added after the experience and capacity rules are approved.

The production integration is designed to:

1. Read allowed categories, items, variations, and modifiers from the existing Square Catalog.
2. Atomically hold the requested pizza capacity for a short checkout window.
3. Create a scheduled Square pickup order with the customer's name, phone, pickup time, and notes.
4. Tokenize payment details with Square's Web Payments SDK.
5. Authorize and capture the payment with an idempotency key and the Square order ID.
6. Convert the temporary capacity hold into a confirmed reservation.
7. Send a branded confirmation through Amazon SES and reconcile payment/order state through Square webhooks.

## Run locally

Python 3.12 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python run.py
```

Then open `http://127.0.0.1:5000`.

Before using anything other than demo mode, generate a private Flask session key and put it in `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run tests with:

```bash
python -m pytest
```

## Configuration

The initial values live in `app/__init__.py` so they are easy to find:

- `ADVANCE_DAYS`: 7
- `SLOT_INTERVAL_MINUTES`: 15
- `PIZZA_SLOT_CAPACITY`: 3
- `CATEGORY_LIMITS`: `{ "pizza": 3 }`
- `CART_TOTAL_LIMIT`: 8
- `SALES_TAX_RATE`: 0.08
- `SERVICE_HOURS`: Thursday/Friday 4–8, Saturday 11–8, Sunday 11–4

Menu categories, representative prototype items, and temporary addition prices live in `app/menu.py`. Square Catalog data will replace those placeholders during the Sandbox phase.

## Git and public-repository safety

Run the included setup once from the project directory:

```bash
bash setup-git.sh
```

It initializes a `master` branch, checks every proposed public file for common secret material, installs the same check as a pre-commit hook, stages the source, and creates the initial commit when your Git name and email are configured. The project uses `master` by default to match the existing Pizzeria Mari Dashboard repository.

Create an empty GitHub repository without adding GitHub's README or `.gitignore`, then connect and push it:

```bash
git remote add origin git@github.com:YOUR-USER/pizzeria-mari-ordering.git
git push -u origin master
```

Local `.env` files, SQLite databases, virtual environments, caches, logs, private-key formats, and downloaded update archives are ignored. GitHub Actions repeats the public-file audit and runs the tests on every push and pull request.

Never place a Square access token, Flask secret key, webhook signature key, or AWS credential in a tracked file. When integrations are added, keep local values in `.env` and production values in the deployment platform's encrypted secret store. Square's server access token must never be sent to browser JavaScript.

The Compagnon and Semplicita font binaries are also excluded from the public repository until their licenses are confirmed to allow source redistribution. Existing local copies continue to work; see `app/static/fonts/README.md` for the required filenames.

## Recommended production shape

Use a serverless AWS deployment:

- API Gateway HTTP API for HTTPS requests.
- AWS Lambda for the Flask application.
- DynamoDB on-demand for slot counters, checkout holds, order state, and idempotency records.
- Amazon SES for confirmation emails.
- EventBridge for expired-hold cleanup and reconciliation.
- CloudWatch alarms for failed payments, webhook errors, and capacity inconsistencies.

These services are managed across multiple Availability Zones and have effectively no idle application-server cost. At Pizzeria Mari's likely request volume, the infrastructure should normally cost only a few dollars per month, excluding the domain, Square processing fees, and unusually verbose logs.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the production transaction design and delivery plan.
