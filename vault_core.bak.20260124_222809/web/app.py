from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from vault_core.web.api import router as api_router

app = FastAPI(
    title="Straightline Datalabs Vault",
    description="People's research vault: ingest → OCR → index → search.",
    version="0.1.0",
)

# For now, be permissive; later we can restrict origins to your domains.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Mount JSON API under /api
app.include_router(api_router, prefix="/api")


# --- Simple HTML UI ---------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Straightline Datalabs Vault</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      --bg: #050816;
      --bg2: #0b1020;
      --accent: #4ade80;
      --accent-soft: rgba(74, 222, 128, 0.12);
      --accent2: #60a5fa;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --danger: #f97373;
      --border: #1f2933;
      --card: #0f172a;
      --radius-lg: 16px;
      --radius-md: 10px;
      --shadow-soft: 0 18px 40px rgba(0,0,0,0.55);
      --shadow-chip: 0 10px 25px rgba(0,0,0,0.5);
      --font-sans: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text",
                   "Segoe UI", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      padding: 24px;
      font-family: var(--font-sans);
      background: radial-gradient(circle at top left, #111827 0, #020617 52%, #000 100%);
      color: var(--text);
    }

    .app-shell {
      max-width: 1180px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 2.4fr) minmax(0, 1.6fr);
      gap: 20px;
    }

    @media (max-width: 900px) {
      .app-shell {
        grid-template-columns: minmax(0, 1fr);
      }
    }

    .card {
      background: linear-gradient(145deg, rgba(15,23,42,0.96), rgba(15,23,42,0.92));
      border-radius: var(--radius-lg);
      border: 1px solid rgba(148,163,184,0.16);
      box-shadow: var(--shadow-soft);
      padding: 18px 18px 16px;
      backdrop-filter: blur(12px);
      position: relative;
      overflow: hidden;
    }

    .card::before {
      content: "";
      position: absolute;
      inset: -20%;
      background:
        radial-gradient(circle at top, rgba(55,65,81,0.20), transparent 60%),
        radial-gradient(circle at bottom left, rgba(79,70,229,0.18), transparent 50%),
        radial-gradient(circle at bottom right, rgba(45,212,191,0.18), transparent 55%);
      opacity: 0.75;
      mix-blend-mode: soft-light;
      pointer-events: none;
    }

    .card-inner {
      position: relative;
      z-index: 1;
    }

    .header-row {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }

    h1 {
      font-size: 1.15rem;
      font-weight: 600;
      letter-spacing: 0.03em;
      display: flex;
      align-items: center;
      gap: 0.45rem;
    }

    .logo-dot {
      width: 9px;
      height: 18px;
      border-radius: 999px;
      background: linear-gradient(180deg, #4ade80, #a855f7);
      box-shadow:
        0 0 30px rgba(74,222,128,0.8),
        0 0 46px rgba(168,85,247,0.9);
    }

    .subtitle {
      font-size: 0.8rem;
      color: var(--muted);
    }

    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 14px;
    }

    .pill {
      font-size: 0.75rem;
      border-radius: 999px;
      padding: 3px 10px;
      border: 1px solid rgba(148,163,184,0.35);
      background: radial-gradient(circle at top left, rgba(148,163,184,0.16), rgba(15,23,42,0.9));
      color: var(--muted);
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }

    .pill span.dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 12px rgba(74,222,128,0.7);
    }

    .pill.badge {
      border-color: rgba(96,165,250,0.6);
      color: #bfdbfe;
    }

    .status-dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 16px rgba(34,197,94,0.9);
    }

    .status-dot.bad {
      background: var(--danger);
      box-shadow: 0 0 16px rgba(239,68,68,0.9);
    }

    .status-text {
      font-size: 0.75rem;
      color: var(--muted);
    }

    .status-text strong {
      color: var(--text);
      font-weight: 500;
    }

    .search-form {
      display: flex;
      gap: 8px;
      margin-bottom: 8px;
    }

    .search-form input[type="text"] {
      flex: 1;
      padding: 9px 10px;
      border-radius: var(--radius-md);
      border: 1px solid rgba(148,163,184,0.6);
      background: rgba(15,23,42,0.95);
      color: var(--text);
      font-size: 0.85rem;
      outline: none;
    }

    .search-form input[type="text"]::placeholder {
      color: rgba(148,163,184,0.7);
    }

    .search-form button {
      padding: 9px 12px;
      border-radius: var(--radius-md);
      border: 0;
      background: radial-gradient(circle at top left, #4ade80, #22c55e);
      color: #022c22;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      box-shadow: var(--shadow-chip);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }

    .search-form button:hover {
      filter: brightness(1.05);
      transform: translateY(-0.5px);
    }

    .search-form button:active {
      transform: translateY(1px);
      box-shadow: 0 6px 18px rgba(0,0,0,0.7);
    }

    .search-form button span.icon {
      font-size: 1rem;
    }

    .helper-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }

    .helper-text {
      font-size: 0.75rem;
      color: var(--muted);
    }

    .helper-text code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 0.72rem;
      background: rgba(15,23,42,0.9);
      padding: 2px 5px;
      border-radius: 6px;
      border: 1px solid rgba(31,41,55,0.7);
      color: #e5e7eb;
    }

    .results {
      border-radius: var(--radius-md);
      border: 1px solid rgba(55,65,81,0.9);
      background: radial-gradient(circle at top left, rgba(15,23,42,0.9), rgba(15,23,42,0.98));
      padding: 10px;
      max-height: 420px;
      overflow: auto;
      font-size: 0.8rem;
    }

    .results-empty {
      color: var(--muted);
      font-size: 0.8rem;
    }

    .result-item {
      padding: 8px 7px 9px;
      border-radius: 8px;
      border: 1px solid transparent;
      margin-bottom: 6px;
      background: rgba(15,23,42,0.92);
      transition: border-color 0.12s ease, background-color 0.12s ease;
    }

    .result-item:hover {
      border-color: rgba(96,165,250,0.7);
      background: rgba(15,23,42,0.99);
    }

    .result-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 4px;
    }

    .result-title {
      font-size: 0.86rem;
      font-weight: 500;
      color: #e5e7eb;
    }

    .result-meta {
      font-size: 0.7rem;
      color: var(--muted);
    }

    .result-score {
      font-size: 0.7rem;
      color: var(--accent2);
    }

    .result-snippet {
      font-size: 0.78rem;
      color: var(--muted);
      line-height: 1.5;
    }

    .result-snippet b.match {
      color: #f97316;
      background: rgba(251,113,133,0.12);
      padding: 0 2px;
      border-radius: 3px;
    }

    .sidebar-title {
      font-size: 0.9rem;
      font-weight: 500;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
    }

    .sidebar-title span.small {
      font-size: 0.74rem;
      color: var(--muted);
      font-weight: 400;
    }

    .manifest-list {
      border-radius: var(--radius-md);
      border: 1px solid rgba(55,65,81,0.9);
      background: rgba(15,23,42,0.97);
      padding: 8px;
      max-height: 260px;
      overflow: auto;
      font-size: 0.76rem;
    }

    .manifest-item {
      padding: 6px 6px 7px;
      border-radius: 8px;
      border: 1px solid transparent;
      margin-bottom: 6px;
      background: rgba(15,23,42,0.98);
    }

    .manifest-item:hover {
      border-color: rgba(74,222,128,0.6);
    }

    .manifest-kind {
      font-size: 0.7rem;
      color: #a5b4fc;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .manifest-url {
      font-size: 0.75rem;
      color: var(--muted);
      word-break: break-all;
      margin-bottom: 2px;
    }

    .manifest-path {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 0.7rem;
      color: #9ca3af;
      opacity: 0.9;
    }

    .timestamp {
      font-size: 0.7rem;
      color: rgba(148,163,184,0.9);
      margin-top: 2px;
    }

    .badge-mini {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid rgba(74,222,128,0.5);
      font-size: 0.7rem;
      color: #bbf7d0;
    }

    .badge-mini span.dot {
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 12px rgba(74,222,128,0.9);
    }

    .section-footer {
      margin-top: 10px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
    }

    .footer-text {
      font-size: 0.72rem;
      color: var(--muted);
    }

    .footer-text strong {
      font-weight: 500;
      color: var(--text);
    }

    .footer-hint {
      font-size: 0.72rem;
      color: var(--muted);
      text-align: right;
    }

    .footer-hint code {
      font-size: 0.7rem;
      background: rgba(15,23,42,0.9);
      border-radius: 6px;
      padding: 2px 5px;
      border: 1px solid rgba(31,41,55,0.7);
    }

    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }

    .chip {
      font-size: 0.7rem;
      border-radius: 999px;
      padding: 2px 7px;
      border: 1px solid rgba(148,163,184,0.4);
      background: rgba(15,23,42,0.96);
      color: var(--muted);
    }

    .chip.safe {
      border-color: rgba(45,212,191,0.7);
      color: #a5f3fc;
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <!-- LEFT: SEARCH PANEL -->
    <section class="card">
      <div class="card-inner">
        <div class="header-row">
          <div>
            <h1>
              <span class="logo-dot"></span>
              Straightline Vault
            </h1>
            <div class="subtitle">
              Ingest → OCR → Index → Search. Your research, under your control.
            </div>
          </div>
          <div>
            <div class="badge-mini" id="api-status-pill">
              <span class="dot"></span>
              <span id="api-status-text">API live</span>
            </div>
          </div>
        </div>

        <div class="pill-row">
          <div class="pill badge">
            <span class="dot"></span>
            <span>Backend: FastAPI · Whoosh · Tesseract</span>
          </div>
          <div class="pill">
            <span class="dot"></span>
            <span>Indexing p463 (IRS travel & mileage)</span>
          </div>
        </div>

        <form class="search-form" id="search-form">
          <input
            id="search-input"
            type="text"
            name="q"
            placeholder="Search your vault (e.g. mileage, travel expenses, per diem)…"
            autocomplete="off"
          />
          <button type="submit">
            <span class="icon">🔎</span>
            <span>Search</span>
          </button>
        </form>

        <div class="helper-row">
          <div class="helper-text">
            Try searches like <code>mileage</code>, <code>travel expenses</code>, or <code>meal allowance</code>.
          </div>
          <div class="status-text">
            <span class="status-dot" id="health-dot"></span>
            <span id="health-text"><strong>Vault</strong> healthy</span>
          </div>
        </div>

        <div class="results" id="results">
          <div class="results-empty" id="results-empty">
            No results yet. Run a search to see snippets from your ingested documents.
          </div>
        </div>

        <div class="section-footer">
          <div class="footer-text">
            <strong>Search is local-only.</strong> Nothing leaves this server unless you choose to export it.
          </div>
          <div class="footer-hint">
            CLI: <code>python scripts/search_cli.py "mileage"</code>
          </div>
        </div>
      </div>
    </section>

    <!-- RIGHT: MANIFEST + PIPELINE -->
    <section class="card">
      <div class="card-inner">
        <div class="sidebar-title">
          <span>Ingestion manifest</span>
          <span class="small" id="manifest-count-label">Latest entries</span>
        </div>

        <div class="manifest-list" id="manifest-list">
          <div class="results-empty">
            Loading manifest…
          </div>
        </div>

        <div class="chip-row">
          <div class="chip safe">8001 is not exposed publicly (UFW locked)</div>
          <div class="chip">intake → /input</div>
          <div class="chip">ocr → /ocr</div>
          <div class="chip">index → Whoosh index dir</div>
        </div>

        <div class="section-footer">
          <div class="footer-text">
            <strong>Provenance-aware.</strong> Each ingest is logged with URL, paths, and timestamp.
          </div>
          <div class="footer-hint">
            API: <code>GET /api/manifest</code>
          </div>
        </div>
      </div>
    </section>
  </div>

  <script>
    const resultsContainer = document.getElementById("results");
    const resultsEmpty = document.getElementById("results-empty");
    const manifestList = document.getElementById("manifest-list");
    const manifestCountLabel = document.getElementById("manifest-count-label");
    const healthDot = document.getElementById("health-dot");
    const healthText = document.getElementById("health-text");
    const apiStatusPill = document.getElementById("api-status-pill");
    const apiStatusText = document.getElementById("api-status-text");

    function setHealth(ok, msg) {
      if (ok) {
        healthDot.classList.remove("bad");
        healthText.innerHTML = "<strong>Vault</strong> healthy";
        apiStatusText.textContent = "API live";
      } else {
        healthDot.classList.add("bad");
        healthText.innerHTML = "<strong>Vault</strong> error";
        apiStatusText.textContent = msg || "API error";
      }
    }

    async function checkHealth() {
      try {
        const [appRes, apiRes] = await Promise.all([
          fetch("/health"),
          fetch("/api/health")
        ]);
        const appJson = await appRes.json();
        const apiJson = await apiRes.json();
        const ok = appJson && appJson.status === "ok" && apiJson && apiJson.api === "ok";
        setHealth(ok);
      } catch (e) {
        console.error("Health check failed:", e);
        setHealth(false, "API unreachable");
      }
    }

    function renderResults(results) {
      resultsContainer.innerHTML = "";
      if (!results || !results.length) {
        const div = document.createElement("div");
        div.className = "results-empty";
        div.textContent = "No matches. Try another term or broaden your query.";
        resultsContainer.appendChild(div);
        return;
      }

      results.forEach((r) => {
        const item = document.createElement("article");
        item.className = "result-item";

        const header = document.createElement("div");
        header.className = "result-header";

        const title = document.createElement("div");
        title.className = "result-title";
        title.textContent = r.doc_id || "Document";

        const meta = document.createElement("div");
        meta.className = "result-meta";
        const score = typeof r.score === "number" ? r.score.toFixed(2) : r.score;
        meta.innerHTML = `<span class="result-score">score=${score}</span> · <span>${r.source_file || ""}</span>`;

        header.appendChild(title);
        header.appendChild(meta);

        const snippet = document.createElement("div");
        snippet.className = "result-snippet";
        snippet.innerHTML = r.snippet || "";

        item.appendChild(header);
        item.appendChild(snippet);
        resultsContainer.appendChild(item);
      });
    }

    async function runSearch(query) {
      if (!query || !query.trim()) {
        renderResults([]);
        return;
      }
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        if (!res.ok) {
          throw new Error("HTTP " + res.status);
        }
        const json = await res.json();
        renderResults(json);
      } catch (e) {
        console.error("Search failed:", e);
        resultsContainer.innerHTML = "";
        const div = document.createElement("div");
        div.className = "results-empty";
        div.textContent = "Search failed. Check logs on the server.";
        resultsContainer.appendChild(div);
      }
    }

    async function loadManifest() {
      try {
        const res = await fetch("/api/manifest");
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        manifestList.innerHTML = "";

        if (!data || !data.length) {
          const div = document.createElement("div");
          div.className = "results-empty";
          div.textContent = "Manifest is empty. Ingest a URL to see entries here.";
          manifestList.appendChild(div);
          manifestCountLabel.textContent = "0 entries";
          return;
        }

        manifestCountLabel.textContent = `${data.length} entr${data.length === 1 ? "y" : "ies"}`;

        data.forEach((rec) => {
          const item = document.createElement("article");
          item.className = "manifest-item";

          const kind = document.createElement("div");
          kind.className = "manifest-kind";
          kind.textContent = (rec.kind || "unknown").toUpperCase();

          const url = document.createElement("div");
          url.className = "manifest-url";
          url.textContent = rec.source_url || "(no source_url)";

          const paths = document.createElement("div");
          paths.className = "manifest-path";
          const pdf = rec.pdf || "(no pdf)";
          const txt = rec.txt || "(no txt)";
          paths.textContent = `pdf: ${pdf} · txt: ${txt}`;

          const ts = document.createElement("div");
          ts.className = "timestamp";
          ts.textContent = rec.timestamp || "";

          item.appendChild(kind);
          item.appendChild(url);
          item.appendChild(paths);
          item.appendChild(ts);
          manifestList.appendChild(item);
        });
      } catch (e) {
        console.error("Manifest load failed:", e);
        manifestList.innerHTML = "";
        const div = document.createElement("div");
        div.className = "results-empty";
        div.textContent = "Failed to load manifest. Check server logs.";
        manifestList.appendChild(div);
        manifestCountLabel.textContent = "Error";
      }
    }

    document.getElementById("search-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const value = document.getElementById("search-input").value;
      runSearch(value);
    });

    // On initial load:
    checkHealth();
    loadManifest();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)
