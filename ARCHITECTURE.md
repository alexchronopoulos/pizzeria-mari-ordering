# Production architecture

## Core decision

Square remains the operational system of record for catalog items, modifiers, taxes, payments, and paid pickup orders. The custom Flask portal owns the customer experience, cart rules, and pickup-slot capacity.

This means a paid portal order appears in Square and remains visible to the existing Pizzeria Mari Production Dashboard. Staff do not need to operate a second order-management system.

## Capacity model

Each pickup slot has an independent counter for each configured capacity category. Initially only `pizza` needs a slot capacity:

```text
2026-08-06T16:15:00-04:00
  pizza capacity: 3
  held: 1
  confirmed: 1
  available: 1
```

The same cart can also enforce:

- a per-category maximum, such as no more than three pizzas;
- a total-item maximum, such as no more than eight items across pizzas, salads, cookies, sides, and drinks.

Both rules are checked in the browser for fast feedback and again on the server. The browser is never trusted as the final authority.

## Safe checkout sequence

Capacity must be claimed before payment without overselling the last pizza spot.

1. Recalculate the cart from current Square catalog IDs and server-side prices.
2. Use one DynamoDB transaction to create a short-lived checkout hold and increment the slot's held pizza count only if `held + confirmed + requested <= 3`.
3. Create a scheduled Square pickup order containing catalog-backed line items, modifiers, customer details, pickup time, prep time, and order notes.
4. Tokenize the card in the browser with Square Web Payments SDK. Raw card data never reaches Flask.
5. Create the Square payment with the order ID, tip, buyer email, and an idempotency key. Use delayed capture while the capacity hold is finalized.
6. Convert the hold to confirmed capacity in a DynamoDB transaction and complete the Square payment.
7. If any step fails, cancel the authorization and release the hold using idempotent compensation logic.
8. Process Square payment and order webhooks to reconcile interrupted requests.
9. Send the confirmation only after paid status is verified.

Expired holds are released both lazily during slot reads and by a once-per-minute cleanup task. DynamoDB TTL can remove old records later, but TTL deletion is not used as the immediate capacity-release mechanism.

## DynamoDB records

A single-table design is sufficient at this scale:

| Record | Partition key | Sort key | Purpose |
| --- | --- | --- | --- |
| Slot | `SLOT#<service_at>` | `CAPACITY` | Capacity, held count, confirmed count, version |
| Hold | `SLOT#<service_at>` | `HOLD#<uuid>` | Requested category counts, status, expiry |
| Order | `ORDER#<uuid>` | `ORDER` | Customer, cart snapshot, Square IDs, payment state |
| Idempotency | `IDEMPOTENCY#<key>` | `REQUEST` | Safe retries and duplicate-submit protection |

The slot update uses a conditional expression. Only one concurrent checkout can claim the last remaining capacity.

## Square responsibilities

- Catalog API: load only explicitly allowed category IDs and their items, variations, modifiers, availability, and prices.
- Modifier lists: normalize Square's whole-pie and half-pie modifier groups into one placement picker in the customer UI, then map the selection back to the correct Square Catalog modifier ID.
- Orders API: create one scheduled `PICKUP` fulfillment, apply Square-backed taxes and catalog discounts, and verify the returned order total before payment.
- Web Payments SDK: secure browser-side card entry and tokenization.
- Payments and Gift Cards APIs: include `order_id`, `tip_money`, `buyer_email_address`, gift-card tender when used, and a unique idempotency key.
- Webhooks: reconcile payments and order changes even if a customer closes the browser at an awkward moment.

## AWS deployment

The preferred deployment is Flask on Lambda behind an API Gateway HTTP API. DynamoDB on-demand provides the shared state needed by concurrent Lambda instances. Amazon SES sends confirmation messages.

This is a better fit than a single inexpensive virtual server because the latter is not redundant. Two EC2 instances plus a load balancer and a redundant SQL database would cost materially more at this traffic level.

## Delivery phases

### Phase 1 — experience prototype

- Approve the menu, pickup-time, item-modal, cart, and checkout experience.
- Confirm the exact cart-wide limit and which categories consume pickup capacity.
- Replace the CSS pizza stand-ins with the user's product photography and branding assets.

### Phase 2 — Square Sandbox

- Connect Catalog, Orders, Web Payments, and Payments APIs.
- Create the first paid scheduled order in Square Sandbox.
- Confirm that its fields appear correctly in the Production Dashboard parser.

### Phase 3 — production capacity

- Add DynamoDB holds and conditional slot counters.
- Add webhook reconciliation and automatic expired-hold release.
- Stress-test simultaneous claims for the last slot.

### Phase 4 — AWS and email

- Deploy with infrastructure as code.
- Verify the sending domain in SES and create branded receipts.
- Add monitoring, backups, budget alerts, and an operational rollback procedure.

### Phase 5 — controlled launch

- Run live payments with a private or unlinked URL.
- Compare the custom portal's orders against Square and the Production Dashboard.
- Move the public ordering link only after totals, taxes, tips, slots, and receipts reconcile.
