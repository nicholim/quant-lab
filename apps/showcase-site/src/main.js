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

function chip(text, variant) {
  const c = el("span", `chip${variant ? ` chip--${variant}` : ""}`, text);
  return c;
}

function renderCard(p, index) {
  const card = el("article", "card");
  card.id = p.repo;

  // Top row: index + live/static status dot
  const top = el("div", "card__top");
  top.appendChild(el("span", "card__index", String(index + 1).padStart(2, "0")));
  const status = el(
    "span",
    `card__status${p.liveDemo ? " is-live" : ""}`,
    p.liveDemo ? "Live demo" : "Library"
  );
  top.appendChild(status);
  card.appendChild(top);

  // Header
  const header = el("div", "card__header");
  header.appendChild(el("h2", "card__title", p.title));
  header.appendChild(el("p", "card__tagline", p.tagline));
  card.appendChild(header);

  // Summary
  card.appendChild(el("p", "card__summary", p.summary));

  // Metric chips
  if (p.metrics?.length) {
    const metrics = el("div", "card__metrics");
    p.metrics.forEach((m) => metrics.appendChild(chip(m, "metric")));
    card.appendChild(metrics);
  }

  // Optional static artifact (order-book-simulator)
  if (p.artifact) {
    const figure = el("figure", "card__figure");
    const img = el("img", "artifact");
    img.src = `/${p.artifact}`;
    img.alt = `${p.title} — static depth-chart visualisation`;
    img.loading = "lazy";
    figure.appendChild(img);
    card.appendChild(figure);
  }

  // Stack tags
  const stack = el("div", "card__stack");
  p.stack.forEach((s) => stack.appendChild(chip(s, "stack")));
  card.appendChild(stack);

  // Expandable "vs" positioning
  const details = el("details", "card__versus");
  const summaryEl = el("summary");
  summaryEl.append(
    el("span", "card__versus-label", "vs"),
    el("span", "card__versus-names", p.versus.vs)
  );
  details.appendChild(summaryEl);
  details.appendChild(el("p", "card__versus-note", p.versus.note));
  card.appendChild(details);

  // Actions
  const actions = el("div", "card__actions");

  const gh = el("a", "btn btn--ghost");
  gh.href = githubUrl(p);
  gh.target = "_blank";
  gh.rel = "noopener noreferrer";
  gh.append(githubIcon(), document.createTextNode("Code"));
  actions.appendChild(gh);

  if (p.liveDemo) {
    const demo = el("a", "btn btn--primary");
    demo.href = p.demoUrl;
    demo.target = "_blank";
    demo.rel = "noopener noreferrer";
    demo.append(document.createTextNode("Live demo"), arrowIcon());
    actions.appendChild(demo);
  } else if (p.demoStatus) {
    actions.appendChild(el("span", "card__note", p.demoStatus));
  }

  card.appendChild(actions);
  return card;
}

function githubIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", "15");
  svg.setAttribute("height", "15");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("btn__icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute(
    "d",
    "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
  );
  svg.appendChild(path);
  return svg;
}

function arrowIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("btn__icon", "btn__icon--arrow");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.75");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M4.5 11.5 11.5 4.5M6 4.5h5.5V10");
  svg.appendChild(path);
  return svg;
}

function renderHero() {
  const hero = el("header", "hero");

  const eyebrow = el("div", "hero__eyebrow");
  eyebrow.append(el("span", "hero__pulse"), document.createTextNode("Quantitative engineering"));
  hero.appendChild(eyebrow);

  const h1 = el("h1", "hero__title");
  h1.append(
    document.createTextNode("Five focused tools for "),
    el("span", "hero__accent", "systematic trading"),
    document.createTextNode(".")
  );
  hero.appendChild(h1);

  hero.appendChild(
    el(
      "p",
      "hero__subtitle",
      "A backtester, a market-data pipeline, an options pricer, a C++ order-book matching " +
        "engine, and a portfolio optimizer — each built to do one thing well, with tests, " +
        "benchmarks, and a runnable demo."
    )
  );

  const stats = el("div", "hero__stats");
  const statData = [
    ["5", "projects"],
    ["2", "languages"],
    ["3", "live demos"],
    ["100%", "open source"],
  ];
  statData.forEach(([num, label]) => {
    const stat = el("div", "stat");
    stat.appendChild(el("span", "stat__num", num));
    stat.appendChild(el("span", "stat__label", label));
    stats.appendChild(stat);
  });
  hero.appendChild(stats);

  const gh = el("a", "btn btn--ghost hero__cta");
  gh.href = REPO_URL;
  gh.target = "_blank";
  gh.rel = "noopener noreferrer";
  gh.append(githubIcon(), document.createTextNode("View the monorepo"));
  hero.appendChild(gh);

  return hero;
}

function renderFooter() {
  const footer = el("footer", "footer");
  const line = el("p", "footer__line");
  line.append(document.createTextNode("Five projects, one monorepo — "));
  const a = el("a");
  a.href = REPO_URL;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = "nicholim/quant-lab";
  line.appendChild(a);
  line.append(document.createTextNode("."));
  footer.appendChild(line);
  return footer;
}

function init() {
  const app = document.getElementById("app");
  app.appendChild(renderHero());

  const grid = el("section", "grid");
  projects.forEach((p, i) => grid.appendChild(renderCard(p, i)));
  app.appendChild(grid);

  app.appendChild(renderFooter());
}

init();
