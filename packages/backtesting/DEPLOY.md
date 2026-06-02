# Deploying the dashboard

`dashboard.py` is a [Dash](https://dash.plotly.com/) app — a long-running Python
web **server** (Flask/WSGI under the hood). That shapes where it can run.

## ⚠️ Netlify won't work

Netlify hosts **static sites and short-lived serverless functions**. It cannot run
a persistent Python server, so the Dash app can't be deployed there as-is.
(You *could* keep a Netlify front-end and call out to the dashboard hosted
elsewhere, but the dashboard itself needs a real Python host.)

## Recommended hosts

Any platform that runs a persistent Python web process works. Easiest first:

| Host | Notes |
|------|-------|
| **Render** | Free web-service tier, Git-based deploys, reads `Procfile`. Simplest. |
| **Railway** | Similar, generous free usage. |
| **Fly.io** | Containers, global; needs a `Dockerfile` or buildpack. |

All run the app via the included `Procfile`:

```
web: gunicorn dashboard:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

`server = app.server` is exported in `dashboard.py` for exactly this.

## The one gotcha: the engine dependency

Locally, `requirements.txt` installs the optimization engine from the sibling
folder (`-e ../portfolio-optimization-engine`). That path **does not exist on a
deploy host**, so use **`requirements-deploy.txt`** instead, which installs the
engine from git:

```
portfolio-optimization-engine @ git+https://github.com/<you>/portfolio-optimization-engine.git
```

So before deploying: **push the engine repo to GitHub** and update that URL.

## Render, step by step

1. Push **both** repos to GitHub (engine first; it's the dependency).
2. In `requirements-deploy.txt`, set the engine git URL to your repo.
3. Render → **New → Web Service** → point at the `backtesting-framework` repo.
4. **Build command:** `pip install -r requirements-deploy.txt`
5. **Start command:** `gunicorn dashboard:server --bind 0.0.0.0:$PORT`
6. Deploy. Render sets `$PORT`; the app binds to it automatically.

## Custom domain via Netlify DNS

If your domain's nameservers are managed by **Netlify DNS**, you do **not** change
nameservers or move the domain. Netlify DNS is just a DNS host and can point a
record at any service — keep Netlify managing the zone and add one record.

Use a **subdomain** (e.g. `lab.yourdomain.com`); the apex can't be a CNAME and
presumably stays on its existing Netlify site.

1. Deploy the app (e.g. Render) → note its hostname, e.g. `your-app.onrender.com`.
2. In the host, add the custom domain `lab.yourdomain.com`.
3. In the **Netlify DNS panel** for your domain, add a record:
   - **Type:** `CNAME`  **Name:** `lab`  **Value:** `your-app.onrender.com`
4. Wait for DNS to propagate; the host (Render) auto-issues a TLS cert for the
   subdomain via ACME. **TLS is handled by the app host, not Netlify.**

This is purely additive — your apex, `www`, and other Netlify-pointed records are
untouched, and pointing one subdomain at an external host is fully supported.

> Alternative (not recommended): a Netlify site can proxy a path to the external
> app with a `200` rewrite in `netlify.toml`/`_redirects`. Dash serves assets from
> absolute paths, so path-proxying is fiddly — prefer the subdomain above.

## Notes

- **Data persistence:** the DuckDB cache lives under `data/` (gitignored) and is
  ephemeral on most hosts — fine, it just re-downloads/recaches on a cold start.
- **First request is slow** (downloads prices, optimizes, backtests). The engine
  now caches price downloads (`POE_CACHE_DIR`), so subsequent requests are fast
  until the dyno/container recycles.
- **Run it locally** anytime with `python dashboard.py` → http://127.0.0.1:8050.
