# Production architecture

## One system of record

Square owns the catalog, prices, taxes, discounts, customers, scheduled pickup orders, payments, tips, receipts, and order history. The Flask app does not maintain a database or copy those records.

Flask owns only the storefront presentation and Pizzeria Mari's simple cart and pickup rules. Its signed browser cookie holds the cart, selected pickup time, and the Square order identifier needed to display the result after payment. It never contains a gift-card number, card number, or Square payment token.

## Pickup availability

The optional `PICKUP_SCHEDULE` configuration replaces selected weekday or
specific-date pickup windows. Every configured window has its own pizza
capacity; gaps do not generate pickup times. A specific date takes precedence
over its weekday, while omitted days retain the built-in service schedule and
default `PIZZA_SLOT_CAPACITY`.

The app searches Square once and uses that result to count pizza quantities from paid `OPEN` and `COMPLETED` orders across every displayed pickup time. Those choices are included in the rendered page, so the picker opens without waiting for another request. A five-second in-process cache collapses closely spaced duplicate reads; it is not a persistent store. Full times remain visible and show `Full`.

Unpaid Square-hosted checkout drafts do not count. An app-created gift-card order counts only after `PayOrder` completes it. This means an abandoned checkout cannot block a pickup time and no cleanup or expiration job is needed.

The selected time is revalidated when the storefront, checkout page, or pickup picker loads. Cart edits compare against the remaining capacity already shown to that customer and do not block on another Square search. The app does not perform a second capacity read immediately before creating the Square checkout. Two customers can therefore choose the same remaining opening at nearly the same time; this matches the deliberately simple launch model for Pizzeria Mari's low, spread-out order volume.

## Hosted Square checkout

1. Flask asks Square to calculate the cart using live catalog items, taxes, and automatic discounts.
2. On submit, Flask calls `CreatePaymentLink` once with the scheduled pickup fulfillment, customer contact information, notes, and Square catalog identifiers.
3. The customer pays on Square's hosted page, where Square provides tips, eligible Marketing coupons, cards, available digital wallets, and its emailed receipt.
4. Square redirects the browser to Flask. Flask retrieves the order and payment once, shows confirmation for a completed payment, and clears the cart.

Square requires an idempotency key on the create call. The app generates a fresh UUID for that individual request and does not store, reuse, or reconcile it.

## Gift-card checkout

1. Flask creates one scheduled Square order.
2. Square's Web Payments SDK tokenizes the gift card directly in the browser.
3. Flask authorizes the gift card with partial authorization enabled.
4. If a balance remains, Square's embedded card field tokenizes a credit or debit card for exactly that remainder.
5. Flask calls `PayOrder` once with the approved payment IDs so Square captures the tenders together and completes one order.

The app does not run payment locks, retries, cancellation, expiration, webhook reconciliation, or background recovery. Square requires fresh idempotency keys on `CreateOrder`, `CreatePayment`, and `PayOrder`; those keys identify only their individual calls and are not retained.

An interrupted partial gift-card payment can remain approved until Square releases it under Square's delayed-payment rules. If this happens, inspect the order and payment in Square before asking the customer to retry.

## Production hosting

DigitalOcean App Platform terminates HTTPS and runs one Gunicorn `gthread` worker with four threads. `/health` is the platform and external-monitoring endpoint. Route 53 remains the DNS provider, and a CNAME such as `order.pizzeriamari.com` points to the App Platform target.

`ORDERING_ENABLED=false` stops new checkout creation and displays the existing Square Online fallback. Existing Square returns and gift-card pages remain available. Paid orders remain safe in Square through deployments or application restarts.
