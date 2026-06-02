import "./style.css";
import { projects, GITHUB_OWNER } from "./projects.js";

const githubUrl = (repo) => `https://github.com/${GITHUB_OWNER}/${repo}`;

function badge(text) {
  const el = document.createElement("span");
  el.className = "badge";
  el.textContent = text;
  return el;
}

function renderCard(p) {
  const card = document.createElement("article");
  card.className = "card";
  card.id = p.repo;

  // Header
  const header = document.createElement("div");
  header.className = "card-header";
  const h2 = document.createElement("h2");
  h2.textContent = p.title;
  const tagline = document.createElement("p");
  tagline.className = "tagline";
  tagline.textContent = p.tagline;
  header.append(h2, tagline);

  // Stack badges
  const badges = document.createElement("div");
  badges.className = "badges";
  p.stack.forEach((s) => badges.appendChild(badge(s)));

  // Description
  const desc = document.createElement("p");
  desc.className = "desc";
  desc.textContent = p.description;

  // Versus positioning
  const versus = document.createElement("p");
  versus.className = "versus";
  const vsLabel = document.createElement("strong");
  vsLabel.textContent = `vs ${p.versus.vs}: `;
  versus.append(vsLabel, document.createTextNode(p.versus.note));

  // Optional static artifact (order-book-simulator)
  let artifactEl = null;
  if (p.artifact) {
    artifactEl = document.createElement("img");
    artifactEl.className = "artifact";
    artifactEl.src = `/${p.artifact}`;
    artifactEl.alt = `${p.title} — static visualisation`;
    artifactEl.loading = "lazy";
  }

  // Actions
  const actions = document.createElement("div");
  actions.className = "actions";

  const gh = document.createElement("a");
  gh.className = "btn btn-ghost";
  gh.href = githubUrl(p.repo);
  gh.target = "_blank";
  gh.rel = "noopener noreferrer";
  gh.textContent = "GitHub";
  actions.appendChild(gh);

  if (p.liveDemo) {
    const demo = document.createElement("a");
    demo.className = "btn btn-primary";
    demo.href = p.demoUrl;
    demo.target = "_blank";
    demo.rel = "noopener noreferrer";
    demo.textContent = "Live demo";
    // Mark as a placeholder until the user fills in the real Render URL.
    demo.dataset.todo = "true";
    demo.title = "TODO: replace placeholder Render URL after deploy";
    actions.appendChild(demo);

    const todo = document.createElement("span");
    todo.className = "todo-pill";
    todo.textContent = "demo URL = TODO";
    actions.appendChild(todo);
  } else {
    const noDemo = document.createElement("span");
    noDemo.className = "no-demo";
    noDemo.textContent = "Static artifact (not a hosted service)";
    actions.appendChild(noDemo);
  }

  // Demo note
  let note = null;
  if (p.demoNote) {
    note = document.createElement("p");
    note.className = "demo-note";
    note.textContent = p.demoNote;
  }

  card.append(header, badges, desc, versus);
  if (artifactEl) card.appendChild(artifactEl);
  card.appendChild(actions);
  if (note) card.appendChild(note);
  return card;
}

function init() {
  const app = document.getElementById("app");

  const hero = document.createElement("header");
  hero.className = "hero";
  hero.innerHTML = `
    <h1>Quant Engineering Portfolio</h1>
    <p class="subtitle">
      Five focused quantitative-finance projects — a backtester, a market-data pipeline,
      an options pricer, an order-book matching engine, and a portfolio optimizer.
    </p>
    <p class="meta">
      Static showcase hosted on Netlify · runnable apps on Render ·
      source on <a href="https://github.com/${GITHUB_OWNER}" target="_blank" rel="noopener noreferrer">GitHub</a>
    </p>
  `;

  const grid = document.createElement("section");
  grid.className = "grid";
  projects.forEach((p) => grid.appendChild(renderCard(p)));

  const footer = document.createElement("footer");
  footer.className = "footer";
  footer.innerHTML = `
    <p>
      "Live demo" buttons point at placeholder Render URLs
      (<code>&lt;repo&gt;.onrender.com</code>) until the services are deployed.
    </p>
  `;

  app.append(hero, grid, footer);
}

init();
