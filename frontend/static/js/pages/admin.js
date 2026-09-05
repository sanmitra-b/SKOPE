import { api } from "../api.js";
import { element, toast } from "../core/dom.js";


const QUALITY_METRICS = [
  ["Agent routing", "agent_route_accuracy"],
  ["Tool routing", "tool_route_accuracy"],
  ["Retrieval hit @8", "retrieval_type_hit_rate_at_k"],
  ["Answer success", "answer_success_rate"],
  ["Claim support", "aggregate_claim_support_rate"],
  ["Citation integrity", "citation_integrity_rate"],
  ["Strict operational pass", "strict_operational_pass_rate"],
];

const asPercent = (value, digits = 0) =>
  value === null || value === undefined
    ? "Not run"
    : `${(Number(value) * 100).toFixed(digits)}%`;

const asDuration = (milliseconds) =>
  milliseconds === null || milliseconds === undefined
    ? "Not run"
    : `${(Number(milliseconds) / 1_000).toFixed(2)}s`;

const dateLabel = (value, includeTime = false) => {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  return includeTime ? parsed.toLocaleString() : parsed.toLocaleDateString();
};

const metricValue = (metrics, key) => {
  const value = metrics?.[key];
  return value === undefined ? null : value;
};


export async function renderAdmin() {
  document.querySelector("#main-content").innerHTML = `
    <div class="page-grid oversight-page">
      <section class="oversight-hero">
        <div>
          <span class="eyebrow">Operational confidence</span>
          <h2>System oversight</h2>
          <p id="benchmark-context" class="subtle">Loading the latest representative evaluation…</p>
        </div>
        <div class="score-ring" id="answer-score-ring" aria-label="Answer success rate">
          <div><strong>—</strong><span>answer success</span></div>
        </div>
      </section>

      <div class="metric-grid oversight-kpis" id="admin-metrics">
        <div class="metric-card"><i class="fa-solid fa-circle-check"></i><span>Answer success</span><strong>—</strong></div>
        <div class="metric-card"><i class="fa-solid fa-shield-halved"></i><span>Strict operational pass</span><strong>—</strong></div>
        <div class="metric-card"><i class="fa-solid fa-magnifying-glass-chart"></i><span>Retrieval hit rate @8</span><strong>—</strong></div>
        <div class="metric-card"><i class="fa-solid fa-database"></i><span>SQL execution success</span><strong>—</strong></div>
        <div class="metric-card"><i class="fa-solid fa-gauge-high"></i><span>Median response latency</span><strong>—</strong></div>
        <div class="metric-card review-metric"><i class="fa-solid fa-flag"></i><span>Open review flags</span><strong>—</strong></div>
      </div>

      <div class="oversight-chart-grid">
        <section class="chart-card">
          <div class="chart-heading"><div><h2>Quality profile</h2><span class="subtle">Latest representative end-to-end run</span></div><i class="fa-solid fa-chart-simple"></i></div>
          <div id="quality-chart" class="bar-chart" aria-label="Evaluation quality metrics"></div>
        </section>
        <section class="chart-card">
          <div class="chart-heading"><div><h2>Latency distribution</h2><span class="subtle">Warm end-to-end response time</span></div><i class="fa-solid fa-stopwatch"></i></div>
          <div id="latency-chart" class="latency-chart" aria-label="Latency distribution"></div>
          <div class="target-note"><span></span>Interactive target: under 5 seconds</div>
        </section>
      </div>

      <div class="oversight-chart-grid activity-grid">
        <section class="chart-card">
          <div class="chart-heading"><div><h2>Agent routing volume</h2><span class="subtle">Observed assistant requests by specialist capability</span></div><i class="fa-solid fa-route"></i></div>
          <div id="agent-chart" class="bar-chart compact-chart"></div>
        </section>
        <section class="chart-card">
          <div class="chart-heading"><div><h2>Application activity</h2><span class="subtle">Persisted localhost usage</span></div><i class="fa-solid fa-wave-square"></i></div>
          <div class="activity-counters" id="activity-counters"></div>
        </section>
      </div>

      <section class="table-card">
        <div class="table-toolbar"><div><h2>Operational benchmark history</h2><span class="subtle">Representative runs with at least 10 questions</span></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Date</th><th>Questions</th><th>Strict pass</th><th>Answer success</th><th>p95 latency</th></tr></thead>
          <tbody id="eval-rows"><tr><td colspan="5" class="empty-row">No benchmark runs yet.</td></tr></tbody>
        </table></div>
      </section>

      <section class="table-card">
        <div class="table-toolbar"><div><h2>Ingestion runs</h2><span class="subtle">Corpus integrity and index promotion history</span></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Started</th><th>Status</th><th>Documents</th><th>Chunks</th><th>Failures</th></tr></thead>
          <tbody id="run-rows"><tr><td colspan="5" class="empty-row">Loading metrics…</td></tr></tbody>
        </table></div>
      </section>
    </div>`;

  try {
    const metrics = await api("/api/admin/metrics");
    if (!document.querySelector("#benchmark-context")) return;
    renderAdminData(metrics);
  } catch (reason) {
    toast(reason.message);
  }
}


function renderAdminData(data) {
  const benchmark = data.latest_benchmark || data.latest_evaluation;
  const metrics = benchmark?.metrics || {};
  const latency = metrics.latency || {};
  const questionCount = benchmark?.question_count || latency.count;
  const context = benchmark
    ? `${questionCount || "—"}-question run · completed ${dateLabel(benchmark.completed_at, true)} · ${benchmark.configuration?.artifact_name || benchmark.version}`
    : "No representative operational benchmark has been recorded.";
  document.querySelector("#benchmark-context").textContent = context;

  const summaryValues = [
    asPercent(metricValue(metrics, "answer_success_rate")),
    asPercent(metricValue(metrics, "strict_operational_pass_rate")),
    asPercent(metricValue(metrics, "retrieval_type_hit_rate_at_k")),
    asPercent(metricValue(metrics, "sql_execution_success_rate")),
    asDuration(latency.median_ms),
    Number(data.open_flags || 0).toLocaleString(),
  ];
  document.querySelectorAll("#admin-metrics strong").forEach((node, index) => {
    node.textContent = summaryValues[index] ?? "—";
  });

  const answerRate = Number(metrics.answer_success_rate || 0);
  const ring = document.querySelector("#answer-score-ring");
  ring.style.setProperty("--score", `${Math.max(0, Math.min(1, answerRate)) * 360}deg`);
  ring.querySelector("strong").textContent = asPercent(
    metricValue(metrics, "answer_success_rate"),
  );

  renderQualityChart(metrics);
  renderLatencyChart(latency);
  renderAgentChart(data.agent_volumes || []);
  renderActivity(data);
  renderEvaluationRuns(data.benchmark_history || data.evaluation_history || []);
  renderIngestionRuns(data.ingestion_runs || []);
}


function renderQualityChart(metrics) {
  const chart = document.querySelector("#quality-chart");
  chart.replaceChildren();
  QUALITY_METRICS.forEach(([label, key]) => {
    const raw = metricValue(metrics, key);
    const value = Math.max(0, Math.min(1, Number(raw || 0)));
    const row = element("div", "bar-row");
    const copy = element("div", "bar-label");
    copy.append(element("span", "", label), element("strong", "", asPercent(raw)));
    const track = element("div", "bar-track");
    const fill = element("div", "bar-fill");
    fill.style.width = `${value * 100}%`;
    track.append(fill);
    row.append(copy, track);
    chart.append(row);
  });
}


function renderLatencyChart(latency) {
  const chart = document.querySelector("#latency-chart");
  chart.replaceChildren();
  const values = [
    ["Best", latency.minimum_ms],
    ["Median", latency.median_ms],
    ["p95", latency.p95_ms],
    ["Worst", latency.maximum_ms],
  ];
  const scale = Math.max(5_000, ...values.map(([, value]) => Number(value || 0)));
  values.forEach(([label, milliseconds]) => {
    const row = element("div", "latency-row");
    row.append(element("span", "", label));
    const track = element("div", "latency-track");
    const fill = element("div", `latency-fill ${Number(milliseconds) > 5_000 ? "over-target" : ""}`);
    fill.style.width = `${Math.max(2, Number(milliseconds || 0) / scale * 100)}%`;
    track.append(fill);
    row.append(track, element("strong", "", asDuration(milliseconds)));
    chart.append(row);
  });
}


function renderAgentChart(volumes) {
  const chart = document.querySelector("#agent-chart");
  chart.replaceChildren();
  if (!volumes.length) {
    chart.append(element("div", "empty-state", "No routed assistant activity has been persisted."));
    return;
  }
  const maximum = Math.max(...volumes.map((item) => Number(item.count || 0)), 1);
  volumes.forEach((volume) => {
    const name = String(volume.agent)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
    const row = element("div", "bar-row");
    const copy = element("div", "bar-label");
    copy.append(element("span", "", name), element("strong", "", Number(volume.count).toLocaleString()));
    const track = element("div", "bar-track agent-track");
    const fill = element("div", "bar-fill agent-fill");
    fill.style.width = `${Number(volume.count || 0) / maximum * 100}%`;
    track.append(fill);
    row.append(copy, track);
    chart.append(row);
  });
}


function renderActivity(data) {
  const container = document.querySelector("#activity-counters");
  container.replaceChildren();
  [
    ["fa-comments", "Conversations", data.conversations],
    ["fa-message", "Persisted answers", data.answers],
    ["fa-clock", "Historical average", asDuration(data.average_latency_ms)],
    ["fa-box-archive", "Ingestion runs", data.ingestion_runs?.length || 0],
  ].forEach(([icon, label, value]) => {
    const card = element("div", "activity-counter");
    card.append(
      element("i", `fa-solid ${icon}`),
      element("strong", "", value ?? "—"),
      element("span", "", label),
    );
    container.append(card);
  });
}


function renderEvaluationRuns(runs) {
  const body = document.querySelector("#eval-rows");
  body.replaceChildren();
  if (!runs.length) {
    appendTextRow(body, ["No representative benchmark runs found.", "", "", "", ""]);
    return;
  }
  runs.forEach((run) => {
    const metrics = run.metrics || {};
    appendTextRow(body, [
      dateLabel(run.completed_at || run.started_at),
      run.question_count || metrics.latency?.count || "—",
      asPercent(metrics.strict_operational_pass_rate),
      asPercent(metrics.answer_success_rate),
      asDuration(metrics.latency?.p95_ms),
    ]);
  });
}


function renderIngestionRuns(runs) {
  const body = document.querySelector("#run-rows");
  body.replaceChildren();
  if (!runs.length) {
    appendTextRow(body, ["No ingestion runs found.", "", "", "", ""]);
    return;
  }
  runs.forEach((run) => {
    const row = element("tr");
    const values = [
      dateLabel(run.started_at, true),
      run.status,
      run.source_document_count,
      run.chunk_count,
      run.failed_document_count,
    ];
    values.forEach((value, index) => {
      const cell = element("td");
      if (index === 1) {
        cell.append(element("span", `badge ${String(value).toLowerCase()}`, value));
      } else {
        cell.textContent = value ?? "—";
      }
      row.append(cell);
    });
    body.append(row);
  });
}


function appendTextRow(body, values) {
  const row = element("tr");
  values.forEach((value) => row.append(element("td", "", value)));
  body.append(row);
}
