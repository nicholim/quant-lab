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

## Live market data & the OFFLINE flag

The dashboard fetches OHLCV from **yfinance** (via `YFinanceDataHandler` and the
optimizer's `run_analysis`). yfinance egress from cloud IPs (including Render) is
frequently rate-limited.

Important difference from the options app: the backtesting data layer **raises**
`MarketDataError` after its retries rather than auto-serving the bundled fixture
on failure. The dashboard catches that and surfaces it as an in-UI error (it does
**not** 500), but a rate-limited deploy will show an error instead of results.

The escape hatch is the **`BACKTESTING_OFFLINE`** env var (also the `--offline`
flag on `main.py`). When set, **both** the dashboard's halves go offline with a
**single flag** — the backtest (`YFinanceDataHandler`) and the optimizer frontier
(`run_analysis`, whose `AnalysisConfig.offline` the dashboard now drives off the
same flag) — so the whole page renders from bundled fixtures without touching the
network. The dashboard's default tickers (`AAPL, MSFT, JPM, AMZN`) and date range
are all covered by the bundled fixtures, so an offline **Run** works out of the box.

**Tradeoff / recommendation (mirrors options-pricing):** we deliberately do **not**
default `BACKTESTING_OFFLINE=1` on Render, so live data shows when egress allows.
A commented-out `envVars` stub for it exists on the `backtesting-dashboard` service
in the root `render.yaml`. Because the backtesting layer raises (no per-request
fixture fallback like the options app has), flip the flag **on** if you want a
guaranteed-deterministic showcase that never errors on cloud rate limits.

## Notes

- **Data persistence:** the DuckDB cache lives under `data/` (gitignored) and is
  ephemeral on most hosts — fine, it just re-downloads/recaches on a cold start.
- **First request is slow** (downloads prices, optimizes, backtests). The engine
  now caches price downloads (`POE_CACHE_DIR`), so subsequent requests are fast
  until the dyno/container recycles.
- **Run it locally** anytime with `python dashboard.py` → http://127.0.0.1:8050.
