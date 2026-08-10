# DigitalOcean production deployment

## 1. Create the app

Connect the Git repository and deploy the `master` branch as one App Platform web service. Use:

```bash
gunicorn --config gunicorn.conf.py run:app
```

Configure the HTTP health check as `/health`. One instance is appropriate for the expected traffic.

## 2. Add environment variables

Copy the production values from `.env.example` into App Platform's encrypted environment variables. At minimum:

```dotenv
DEMO_MODE=false
FLASK_DEBUG=false
SECRET_KEY=one-stable-random-value-at-least-32-characters
ORDERING_ENABLED=false
FALLBACK_ORDERING_URL=https://your-existing-square-online-site.example
SQUARE_ENVIRONMENT=production
SQUARE_CATALOG_ENABLED=true
SQUARE_LOCATION_ID=your-production-location-id
SQUARE_ACCESS_TOKEN=your-production-access-token
SQUARE_APPLICATION_ID=your-production-application-id
PUBLIC_BASE_URL=https://order.pizzeriamari.com
PIZZA_CART_LIMIT=3
PIZZA_SLOT_CAPACITY=3
CART_TOTAL_LIMIT=8
PICKUP_SCHEDULE=
```

Keep `SECRET_KEY` unchanged across deployments. Leave `ORDERING_ENABLED=false` through private Production testing.

`PICKUP_SCHEDULE` is optional. For App Platform, enter raw JSON without shell or
`.env` quote characters. The complete format and examples are in `README.md`.
Changing it creates a new deployment; verify the displayed pickup days, times,
and capacities before setting `ORDERING_ENABLED=true`.

## 3. Keep DNS in Route 53

In DigitalOcean, open **Networking → Domains → Add domain**, enter `order.pizzeriamari.com`, and choose **You manage your domain**. DigitalOcean displays the target.

Create this Route 53 record:

| Route 53 field | Value |
| --- | --- |
| Record name | `order.pizzeriamari.com` |
| Record type | `CNAME` |
| Value | The `*.ondigitalocean.app` target DigitalOcean displays |
| TTL | `300` during rollout |

Wait for DigitalOcean to show the domain and managed TLS certificate as active. `PUBLIC_BASE_URL` must be that exact HTTPS origin with no path or trailing slash.

## 4. Verify the private deployment

Check health:

```bash
curl --fail --show-error https://order.pizzeriamari.com/health
```

It should report version `0.17.1` and `ordering_enabled: false`. The home page should show the Square fallback link.

Then run the Sandbox test matrix and these controlled Production transactions at the unadvertised URL:

1. Hosted card payment with a tip.
2. Order fully covered by a Square gift card.
3. Split gift-card/card order.

Verify every order, pickup time, item, modifier, tax, discount, payment, and receipt in Square and the Pizzeria Mari Production Dashboard. Rehearse one DigitalOcean rollback.

## 5. Open or pause ordering

Set `ORDERING_ENABLED=true` and redeploy to open ordering. Set it back to `false` to pause new checkouts and show the existing Square Online fallback.

Monitor `/health` every minute during service and alert on deployment failures, restarts, or HTTP errors. Soft-launch to trusted regulars before replacing the public Square Online link.
