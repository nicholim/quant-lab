# Deployment

This is a [Streamlit](https://streamlit.io/) app (`app.py`). The core calculator needs
no external services. The optional **"Live market" tab** fetches real option data over
the network (see [Live market data](#live-market-data) below) — it degrades gracefully
and needs no secrets to boot.

The single source of truth for the Render service is the **root `render.yaml`** at the
monorepo root (this package has no `render.yaml` of its own); its `options-pricing-calculator`
entry sets `rootDir: packages/options-pricing`. Two hosting paths are documented below; pick one.

## Option A — Streamlit Community Cloud (simplest, recommended)

The fastest zero-config path for a Streamlit app.

1. Push this repo to GitHub (it must be public, or you grant Streamlit access).
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Click **New app** -> pick this repo, branch `main`, main file path `app.py`.
4. Click **Deploy**. Community Cloud reads `requirements.txt` automatically and
   gives you a public `*.streamlit.app` URL.

No `render.yaml`, port, or server flags are needed here — the platform handles them.

## Option B — Render (web service via Blueprint)

Use this if you want everything on one Render account alongside the other quant apps.
The `render.yaml` blueprint in this repo defines the service.

1. Push this repo to GitHub.
2. Render Dashboard -> **New** -> **Blueprint** -> connect this repo.
   Render reads `render.yaml` and provisions the `options-pricing-calculator` web service.
3. Wait for the build (`pip install -r requirements.txt`) and first deploy.
4. Render assigns a public `*.onrender.com` URL.

### Why these flags

Render provides the listen port in `$PORT` and expects the process to bind `0.0.0.0`:

```
streamlit run app.py \
  --server.port $PORT \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --browser.gatherUsageStats false
```

- `--server.headless true` — no attempt to open a local browser / email prompt.
- `--server.enableCORS false` + `--server.enableXsrfProtection false` — required so the app
  works behind Render's TLS-terminating proxy.
- `--browser.gatherUsageStats false` — disable telemetry.
- Health check path is `/_stcore/health` (Streamlit's built-in health endpoint).

### Notes

- Free plan instances spin down on inactivity; the first request after idle is slow (cold start).
- After deploy, wire the showcase site's "Live demo" button to the resulting URL.

## Live market data

The "Live market" tab prices **real** options from free data sources:

- **Option chains + expirations** come from **yfinance** — keyless, no setup.
- **Underlying spot quotes** come from **Finnhub** when the `FINNHUB_API_KEY` env var is
  set, otherwise spot falls back to yfinance. Finnhub is used for spot quotes only (its
  free tier has no full option chains).

### `FINNHUB_API_KEY` (secret, optional but recommended on Render)

1. Get a free key at https://finnhub.io (free tier covers real-time quotes).
2. The root `render.yaml` already declares `FINNHUB_API_KEY` with `sync: false` on the
   `options-pricing-calculator` service — i.e. it is a Render **secret** with no value in
   the repo (never hardcode a key).
3. After the Blueprint applies, open the service in the Render dashboard ->
   **Environment** -> set `FINNHUB_API_KEY` to your key -> save (triggers a redeploy).

If you skip this, the app still works: spot falls back to yfinance.

### Cloud rate-limit caveat & the offline flag

yfinance egress from cloud IPs (including Render) is frequently rate-limited. This is why
spot uses Finnhub as its primary source. For chains there is no keyless alternative, so the
"Live market" tab is **best-effort** on Render:

- The app **already falls back to a bundled sample chain** (`src/data/sample_chain.csv`) when
  a live fetch fails, so the demo never hard-crashes.
- To **force** the deterministic offline demo (skip the network entirely), set
  `OPTIONS_PRICING_OFFLINE=1` (env var; also available as the `--offline` CLI flag for
  `main.py`). A commented-out `envVars` stub for this exists in the root `render.yaml`.

**Tradeoff / recommendation:** we deliberately do **not** default `OPTIONS_PRICING_OFFLINE=1`
on Render, because that would hide the live feature behind a static fixture. The graceful
per-request fallback already prevents crashes, so the default lets live data work when egress
allows and quietly degrades to the sample chain when it doesn't. Flip the flag on only if you
want a guaranteed-deterministic showcase that never touches the network.
