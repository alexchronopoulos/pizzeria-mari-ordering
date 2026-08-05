# Production architecture

## System of record

Square owns every piece of durable business data:

- catalog categories, items, variations, descriptions, images, prices, availability, and modifiers;
- taxes and automatic catalog discounts;
- buyers associated with payments;
- scheduled pickup orders, order notes, payments, tips, and receipt URLs;
- the order history used to calculate how many pizzas are already assigned to a pickup slot.

Flask owns only presentation and rules. Its signed browser cookie contains the current cart and pickup selection. No application database contains catalog, cart, customer, payment, or order records.

The only production-side state outside Square is an expiring concurrency lease. A lease is coordination, not a business-data copy: it contains a pickup-slot key, a random owner value, and an expiry timestamp.

## Capacity calculation

For a requested pickup time, the app searches Square orders for the configured location, keeps scheduled pickup fulfillments for that exact time, and counts line-item quantities whose Square variation IDs belong to configured pizza categories.

The customer sees only whether the time can fit the current cart. Full times stay visible as `Full`; partially occupied times that cannot fit the current cart stay visible as `Unavailable`. Remaining production counts are never returned to the browser.

Cart-wide and per-category limits are checked in the browser for immediate feedback and checked again against the current Square catalog on the server.

## Safe concurrent checkout

Square does not offer an atomic “create this order only if category capacity remains” operation. A short-lived per-slot lease closes that gap across redundant application instances:

1. Tokenize card details in the browser with Square Web Payments SDK. Raw card data never reaches Flask.
2. Acquire the DynamoDB lease for the selected pickup slot with a conditional write.
3. Re-read scheduled orders from Square and reject checkout if the cart no longer fits.
4. Ask Square to calculate the current catalog price, taxes, and automatic discounts.
5. Create an open Square order with catalog variation IDs, catalog modifier IDs, buyer details, notes, and scheduled pickup fulfillment. Its idempotency key is unique to the browser checkout attempt.
6. Verify that Square's created order total matches the amount the buyer reviewed.
7. Create one Square payment referencing that order and using a second idempotency key. Staff gratuity is sent as `tip_money`.
8. On a definite payment failure, cancel the unpaid Square order before releasing the lease.
9. On success, release the lease. The paid Square order is now the durable capacity record.
10. If the payment result is ambiguous because of a network failure, leave the Square order intact, release only after reconciliation, and favor temporarily blocking capacity over overselling it.

Because every checkout for the same slot holds the same lease while it checks capacity and creates its Square order, a second checkout sees the first order before it can make its own capacity decision.

## DynamoDB lease shape

Only one record type is required:

| Field | Example | Purpose |
| --- | --- | --- |
| Partition key | `SLOT#2026-08-06T16:15:00-04:00` | One lock per pickup slot |
| Owner | random UUID | Prevents one request from releasing another's lease |
| Expires at | Unix timestamp | Recovers abandoned leases |

No customer name, email, phone, cart, catalog object, amount, payment ID, or order ID is stored in DynamoDB.

## Square responsibilities

- Catalog API loads only explicitly allowed regular categories and their items, variations, images, sold-out state, and attached modifier lists.
- Square's Whole Pie Additions list is the canonical visible option set. Matching First Half and Second Half list entries supply the placement-specific catalog IDs and prices, and all three lists render as one picker.
- Square's `-1` item-level modifier sentinel inherits the list-level minimum and maximum; a zero maximum is normalized as unlimited before browser or server validation.
- Modifier lists configured as customer-hidden in `.env` are filtered in memory and never copied into application storage.
- CalculateOrder supplies taxes and automatic catalog discounts shown at checkout.
- Orders API creates the scheduled `PICKUP` fulfillment.
- Web Payments SDK securely renders card entry and creates a single-use token.
- Payments API charges the card, records the staff tip, associates the payment with the order, and returns the receipt URL.
- SearchOrders supplies durable pickup-slot usage.
- Webhooks reconcile interrupted requests and external Square order changes.

## AWS deployment

- API Gateway HTTP API terminates HTTPS.
- Lambda runs Flask across redundant infrastructure.
- DynamoDB on-demand provides only the expiring checkout lease.
- SES sends a confirmation containing Square's receipt URL without persisting recipient or order data.
- EventBridge triggers reconciliation for ambiguous attempts if webhooks do not resolve them first.
- CloudWatch alarms on payment, webhook, catalog, and lease failures.

This keeps idle cost very low while removing the single-host failure mode. There is no SQL database and no duplicated application-side order store.

## Delivery phases

### Completed — interface and local rules

- Menu, pickup-time, item modal, cart, checkout, tip, notes, and full-slot experience.
- Three-pizza cart and slot behavior.
- Branding, fonts, responsive layout, public-repository safeguards, and automated tests.

### Current — Square connection

- Live Catalog parsing and exact-category filtering.
- Square modifier normalization and server validation.
- Square order-derived slot counts.
- Square pricing, scheduled order creation, Web Payments card tokenization, and idempotent payment creation.
- Sandbox test coverage with no real credentials in the repository.

### Next — production concurrency and reconciliation

- DynamoDB lease implementation.
- Payment and order webhooks.
- Ambiguous-payment reconciliation and operational alerts.
- Confirmation email containing the Square receipt link.
- Gift-card split tender and the final coupon policy.

### Final — controlled launch

- Deploy with infrastructure as code.
- Exercise simultaneous last-slot claims.
- Reconcile taxes, tips, receipts, order notes, and Production Dashboard output.
- Run live payments at a private URL before changing the public ordering link.
