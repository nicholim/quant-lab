# Quant Showcase Site

A lightweight static portfolio landing page presenting the five projects in the quant
workspace. Built with **Vite** (vanilla JS — no framework, no backend) and deployed as a
static site on **Netlify**. The "Live demo" buttons link out to the runnable apps hosted
on **Render**.

This is part of the Netlify + Render hybrid deployment topology (see the workspace
`AGENTS.md`): Netlify serves this static showcase; Render runs the actual Python apps.

## The 5 projects featured

| Project | Live demo |
|---------|-----------|
| backtesting-framework | Render (Dash) |
| market-data-pipeline | Render (worker — no public UI) |
| options-pricing-calculator | Render (Streamlit) |
| order-book-simulator | None — static visualisation only (WASM = future work) |
| portfolio-optimization-engine | Render (FastAPI) |

## Local development

```bash
npm install
npm run dev      # local dev server with HMR
npm run build    # production build into dist/
npm run preview  # serve the built dist/ locally
```

Project metadata (titles, stacks, descriptions, "vs" positioning, demo URLs) lives in a
single source of truth: [`src/projects.js`](./src/projects.js). Edit that file to update
cards.

## TODO before / after deploy: fill in the real demo URLs

The `demoUrl` values in `src/projects.js` are **placeholders** of the form
`https://<repo>.onrender.com`. They are intentionally fake. Once you deploy each service on
Render, copy the real URL Render assigns and replace the placeholder. The UI marks each
unfilled demo button with a "demo URL = TODO" pill so it is obvious what is still pending.

## Deploy to Netlify

You connect your own Netlify account — these steps assume you have a GitHub repo for this
folder (see below) and a Netlify account.

1. Push this repo to GitHub (e.g. `nicholim/quant-showcase-site`).
2. In the Netlify dashboard: **Add new site → Import an existing project → GitHub**, then
   pick the repo.
3. Netlify auto-detects settings from `netlify.toml`:
   - Build command: `npm run build`
   - Publish directory: `dist`
   - Node version: 22 (pinned via `netlify.toml` and `.nvmrc`)
4. Click **Deploy**. Netlify rebuilds automatically on every push to the default branch.

Alternatively, via the Netlify CLI:

```bash
npm install -g netlify-cli
netlify init      # link to a site
netlify deploy --build --prod
```

## Repo setup (this folder is its own git repo)

This folder is initialised as a standalone git repo (it is **not** committed into any of the
five project repos and the workspace root is not a repo). To create the GitHub remote
yourself:

```bash
cd showcase-site
gh repo create nicholim/quant-showcase-site --private --source=. --remote=origin
git push -u origin main
git push origin feature/agent-improvements
```

Or create the empty repo in the GitHub dashboard and add it as a remote manually. The agent
that scaffolded this site does **not** create remotes or push.
