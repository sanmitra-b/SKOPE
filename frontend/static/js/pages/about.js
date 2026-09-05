export function renderAbout() {
  document.querySelector("#main-content").innerHTML = `
    <div class="about-container">
      <section class="about-hero">
        <div class="about-hero-content">
          <span class="eyebrow"><i class="fa-solid fa-diagram-project"></i> Evidence-grounded operational intelligence</span>
          <h1 style="margin:12px 0 6px;font-size:28px;font-weight:800;letter-spacing:-0.03em;color:var(--text)">SKOPE</h1>
          <h2 style="margin:0 0 12px;font-size:15px;font-weight:600;color:var(--mint-2);text-transform:uppercase;letter-spacing:0.06em">
            Supply Chain Knowledge &amp; Operations AI Engine
          </h2>
          <p style="margin:0;font-size:13.5px;color:var(--muted);line-height:1.65">
            An enterprise supply-chain copilot that combines 1.32 million operational records and 16,200 business documents to deliver fast, cited, decision-ready answers.
          </p>
          <div class="about-tag-strip">
            <span class="about-tag"><i class="fa-solid fa-robot"></i> 6 specialist agents</span>
            <span class="about-tag"><i class="fa-solid fa-database"></i> Guarded SQL</span>
            <span class="about-tag"><i class="fa-solid fa-magnifying-glass"></i> Hybrid RAG</span>
            <span class="about-tag"><i class="fa-solid fa-shield-halved"></i> Claim verification</span>
          </div>
        </div>
      </section>

      <!-- KPI Stats Row -->
      <div class="about-stats-row">
        <div class="about-stat">
          <strong>16,200</strong>
          <span>enterprise documents</span>
        </div>
        <div class="about-stat-divider"></div>
        <div class="about-stat">
          <strong>1.32M</strong>
          <span>source records</span>
        </div>
        <div class="about-stat-divider"></div>
        <div class="about-stat">
          <strong>2.95s</strong>
          <span>median latency</span>
        </div>
        <div class="about-stat-divider"></div>
        <div class="about-stat">
          <strong>100%</strong>
          <span>claim support rate</span>
        </div>
        <div class="about-stat-divider"></div>
        <div class="about-stat">
          <strong>100%</strong>
          <span>routing accuracy</span>
        </div>
      </div>

      <!-- How It Works — 3 Steps -->
      <section class="about-section">
        <div class="about-section-header">
          <i class="fa-solid fa-gears"></i>
          <h3>How it works</h3>
        </div>
        <div class="about-steps">
          <div class="about-step">
            <div class="about-step-num">01</div>
            <div class="about-step-icon"><i class="fa-solid fa-magnifying-glass"></i></div>
            <div class="about-step-body">
              <strong>Retrieve</strong>
              <p>Dense + sparse hybrid search over 17,412 vector chunks finds the strongest source passages. Exact business IDs short-circuit vector search entirely.</p>
            </div>
          </div>
          <div class="about-step-arrow"><i class="fa-solid fa-chevron-right"></i></div>
          <div class="about-step">
            <div class="about-step-num">02</div>
            <div class="about-step-icon"><i class="fa-solid fa-database"></i></div>
            <div class="about-step-body">
              <strong>Compile</strong>
              <p>A deterministic query planner converts intent to a typed plan. AST-validated read-only SQL runs over 8 curated PostgreSQL views — Gemini never writes SQL.</p>
            </div>
          </div>
          <div class="about-step-arrow"><i class="fa-solid fa-chevron-right"></i></div>
          <div class="about-step">
            <div class="about-step-num">03</div>
            <div class="about-step-icon"><i class="fa-solid fa-shield-halved"></i></div>
            <div class="about-step-body">
              <strong>Verify</strong>
              <p>One bounded Gemini call synthesises cited atomic claims. Each claim is verified deterministically via regex and Decimal matching before any answer reaches you.</p>
            </div>
          </div>
        </div>
      </section>

      <!-- 6 Specialist Agents -->
      <section class="about-section">
        <div class="about-section-header">
          <i class="fa-solid fa-robot"></i>
          <h3>Specialist agents</h3>
        </div>
        <div class="about-agents-grid">
          <div class="about-agent-card">
            <i class="fa-solid fa-handshake"></i>
            <strong>Supplier</strong>
            <span>Reliability, contracts, SLA terms, pricing agreements</span>
          </div>
          <div class="about-agent-card">
            <i class="fa-solid fa-truck"></i>
            <strong>Logistics</strong>
            <span>Shipment tracking, carrier performance, port delays</span>
          </div>
          <div class="about-agent-card">
            <i class="fa-solid fa-boxes-stacked"></i>
            <strong>Inventory</strong>
            <span>Stock levels, safety-stock exposure, replenishment</span>
          </div>
          <div class="about-agent-card">
            <i class="fa-solid fa-chart-bar"></i>
            <strong>Analytics</strong>
            <span>Aggregations, spend analysis, historical trends</span>
          </div>
          <div class="about-agent-card">
            <i class="fa-solid fa-triangle-exclamation"></i>
            <strong>Risk</strong>
            <span>Operational risk scoring, audit deficiencies, vulnerabilities</span>
          </div>
          <div class="about-agent-card">
            <i class="fa-solid fa-file-lines"></i>
            <strong>Executive</strong>
            <span>Cross-functional briefings, scorecards, CSV/PDF exports</span>
          </div>
        </div>
      </section>

      <!-- Tech Stack Footer -->
      <div class="about-tech-bar">
        <span><i class="fa-solid fa-bolt"></i> FastAPI</span>
        <span><i class="fa-solid fa-brain"></i> Gemini Flash-Lite</span>
        <span><i class="fa-solid fa-circle-nodes"></i> LangGraph</span>
        <span><i class="fa-solid fa-cube"></i> Qdrant</span>
        <span><i class="fa-solid fa-database"></i> PostgreSQL</span>
        <span><i class="fa-solid fa-microchip"></i> ONNX CUDA</span>
        <span><i class="fa-brands fa-js"></i> Vanilla JS</span>
      </div>
    </div>`;
}
