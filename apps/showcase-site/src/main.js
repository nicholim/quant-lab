import "./style.css";
import { projects, REPO_URL } from "./projects.js";

// All projects live in one monorepo — deep-link into each project's subtree.
const githubUrl = (p) => `${REPO_URL}/tree/main/${p.path}`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function arrow() {
  const s = el("span", "arrow", "→");
  s.setAttribute("aria-hidden", "true");
  return s;
}

const EQUITY_SVG = `
<svg viewBox="0 0 240 184" role="img" aria-label="A backtest equity curve: a systematic strategy's cumulative return rising through drawdowns, above a flatter benchmark line.">
  <line x1="34" y1="16" x2="34" y2="156" stroke="currentColor" stroke-opacity="0.35" stroke-width="1"/>
  <line x1="34" y1="156" x2="226" y2="156" stroke="currentColor" stroke-opacity="0.35" stroke-width="1"/>
  <g stroke="currentColor" stroke-opacity="0.16" stroke-dasharray="2 5">
    <line x1="34" y1="120" x2="226" y2="120"/>
    <line x1="34" y1="84" x2="226" y2="84"/>
    <line x1="34" y1="48" x2="226" y2="48"/>
  </g>
  <polyline fill="none" stroke="currentColor" stroke-opacity="0.32" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"
    points="40,150 72,143 104,135 136,127 168,118 200,109 224,103"/>
  <polyline fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"
    points="40,150 56,139 70,130 84,121 94,128 108,112 124,97 140,84 152,93 168,73 186,59 206,47 224,40"/>
  <circle cx="224" cy="40" r="4.5" fill="var(--accent)"/>
  <text x="118" y="68" font-family="var(--font-mono)" font-size="10" fill="var(--accent)">strategy</text>
  <text x="150" y="132" font-family="var(--font-mono)" font-size="9.5" fill="currentColor" fill-opacity="0.5">benchmark</text>
  <text x="40" y="172" font-family="var(--font-mono)" font-size="9.5" fill="currentColor" fill-opacity="0.5">time &#8594;</text>
  <text x="34" y="12" font-family="var(--font-mono)" font-size="9.5" fill="currentColor" fill-opacity="0.5" text-anchor="middle">equity</text>
</svg>`;

function renderMasthead() {
  const head = el("header", "masthead");

  const figure = el("div", "masthead__figure");
  figure.setAttribute("aria-hidden", "false");
  figure.innerHTML = EQUITY_SVG;
  head.appendChild(figure);

  const block = el("div", "masthead__block");
  const h1 = el("h1", "masthead__title");
  h1.append(
    document.createTextNode("Five focused tools for "),
    el("em", "masthead__em", "systematic trading"),
    document.createTextNode(".")
  );
  block.appendChild(h1);

  block.appendChild(
    el(
      "p",
      "masthead__lede",
      "An event-driven backtester, a market-data pipeline, an options pricer, a C++ " +
        "order-book matching engine, and a portfolio optimizer. Each does one job in a " +
        "trading stack and does it properly: tested, benchmarked, and runnable."
    )
  );

  const meta = el("p", "masthead__meta");
  const repo = el("a", "masthead__repo");
  repo.href = REPO_URL;
  repo.target = "_blank";
  repo.rel = "noopener noreferrer";
  repo.append(document.createTextNode("nicholim/quant-lab"), arrow());
  meta.append(
    repo,
    el("span", "masthead__sep", "·"),
    el("span", null, "one monorepo, Python & C++")
  );
  block.appendChild(meta);

  head.appendChild(block);
  return head;
}

function renderRow(p, index) {
  const row = el("li", "proj");
  row.id = p.repo;
  row.style.setProperty("--i", String(index));

  // --- Main column ---
  const main = el("div", "proj__main");

  const title = el("h2", "proj__title", p.title);
  main.appendChild(title);

  main.appendChild(el("p", "proj__tagline", p.tagline));
  main.appendChild(el("p", "proj__summary", p.summary));

  // Static depth-chart figure (order-book only)
  if (p.artifact) {
    const figure = el("figure", "proj__figure");
    const img = el("img", "depth");
    img.src = `/${p.artifact}`;
    img.alt = `${p.title}: cumulative order-book depth, bids in ink and asks in red around the mid price.`;
    img.loading = "lazy";
    figure.appendChild(img);
    figure.appendChild(
      el("figcaption", "proj__figcaption", "Cumulative book depth — illustrative.")
    );
    main.appendChild(figure);
  }

  // Comparison (expandable)
  const vs = el("details", "proj__vs");
  const summary = el("summary", "proj__vs-summary");
  summary.append(
    document.createTextNode("Compared to "),
    el("span", "proj__vs-names", p.versus.vs)
  );
  vs.appendChild(summary);
  vs.appendChild(el("p", "proj__vs-note", p.versus.note));
  main.appendChild(vs);

  main.appendChild(el("p", "proj__stack", p.stack.join("  ·  ")));

  // --- Rail (mono data + actions) ---
  const rail = el("aside", "proj__rail");

  const metrics = el("ul", "metrics");
  p.metrics.forEach((m, mi) => {
    const li = el("li", mi === 0 ? "metric metric--lead" : "metric", m);
    metrics.appendChild(li);
  });
  rail.appendChild(metrics);

  const status = el(
    "p",
    `proj__status${p.liveDemo ? " is-live" : ""}`,
    p.liveDemo ? "Live demo" : "Library"
  );
  rail.appendChild(status);

  const actions = el("div", "proj__actions");
  if (p.liveDemo) {
    const demo = el("a", "act act--primary");
    demo.href = p.demoUrl;
    demo.target = "_blank";
    demo.rel = "noopener noreferrer";
    demo.append(document.createTextNode("Open demo"), arrow());
    actions.appendChild(demo);
  } else if (p.demoStatus) {
    actions.appendChild(el("p", "proj__note", p.demoStatus));
  }

  const code = el("a", "act act--ghost");
  code.href = githubUrl(p);
  code.target = "_blank";
  code.rel = "noopener noreferrer";
  code.append(document.createTextNode("View code"), arrow());
  actions.appendChild(code);

  rail.appendChild(actions);

  row.append(main, rail);
  return row;
}

function init() {
  const app = document.getElementById("app");
  app.appendChild(renderMasthead());

  const list = el("ol", "ledger");
  projects.forEach((p, i) => list.appendChild(renderRow(p, i)));
  app.appendChild(list);
}

init();
