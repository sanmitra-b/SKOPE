import { api, download } from "../api.js";
import { element, toast } from "../core/dom.js";


function reportPath(extension) {
  const tier = document.querySelector("#risk-filter").value;
  const query = tier ? `?risk_tier=${encodeURIComponent(tier)}` : "";
  return `/api/reports/suppliers.${extension}${query}`;
}


export async function renderReports() {
  document.querySelector("#main-content").innerHTML = `
    <div class="page-grid">
      <div class="metric-grid" id="report-metrics">
        <div class="metric-card"><span>Suppliers</span><strong>—</strong></div>
        <div class="metric-card"><span>Open purchase orders</span><strong>—</strong></div>
        <div class="metric-card"><span>Late shipments</span><strong>—</strong></div>
        <div class="metric-card"><span>Average on-time</span><strong>—</strong></div>
      </div>
      <section class="table-card">
        <div class="table-toolbar">
          <div>
            <h2>Supplier scorecard</h2>
            <span class="subtle">Live from the guarded analytics view</span>
          </div>
          <div style="display:flex;gap:10px;align-items:center">
            <button class="secondary" id="export-scorecard-pdf" style="width:auto;padding:9px 12px">Export PDF</button>
            <button class="secondary" id="export-scorecard" style="width:auto;padding:9px 12px">Export CSV</button>
            <select id="risk-filter" style="width:auto">
              <option value="">All risk tiers</option>
              <option>HIGH</option><option>MEDIUM</option><option>LOW</option>
            </select>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Supplier</th><th>Risk</th><th>Country</th><th>On-time</th><th>Late shipments</th><th>Open POs</th></tr></thead>
            <tbody id="supplier-rows"><tr><td colspan="6" class="empty-row">Loading scorecard…</td></tr></tbody>
          </table>
        </div>
      </section>
    </div>`;

  document.querySelector("#risk-filter").addEventListener("change", loadSupplierReport);
  document.querySelector("#export-scorecard").addEventListener("click", () => {
    download(reportPath("csv"), "skope_supplier_scorecard.csv").catch((reason) => {
      toast(reason.message);
    });
  });
  document.querySelector("#export-scorecard-pdf").addEventListener("click", () => {
    download(reportPath("pdf"), "skope_supplier_scorecard.pdf").catch((reason) => {
      toast(reason.message);
    });
  });
  await loadSupplierReport();
}


function appendSupplierRow(tableBody, row) {
  const tableRow = document.createElement("tr");
  const values = [
    row.supplier_name,
    row.risk_tier,
    row.country,
    row.on_time_percentage === null ? "Unknown" : `${row.on_time_percentage}%`,
    row.late_shipment_count,
    row.open_purchase_order_count,
  ];
  values.forEach((value, index) => {
    const cell = document.createElement("td");
    if (index === 1) {
      cell.append(
        element("span", `badge ${(value || "").toLowerCase()}`, value || "Unrated"),
      );
    } else {
      cell.textContent = value ?? "—";
    }
    tableRow.append(cell);
  });
  tableBody.append(tableRow);
}


function reportMetrics(rows) {
  const ratedRows = rows.filter((row) => row.on_time_percentage !== null);
  const averageOnTime = ratedRows.length
    ? `${(
      ratedRows.reduce((sum, row) => sum + Number(row.on_time_percentage || 0), 0) /
      ratedRows.length
    ).toFixed(1)}%`
    : "Unknown";
  return [
    rows.length,
    rows.reduce((sum, row) => sum + Number(row.open_purchase_order_count || 0), 0),
    rows.reduce((sum, row) => sum + Number(row.late_shipment_count || 0), 0),
    averageOnTime,
  ];
}


async function loadSupplierReport() {
  const tier = document.querySelector("#risk-filter")?.value || "";
  const query = tier ? `?risk_tier=${encodeURIComponent(tier)}` : "";
  try {
    const rows = await api(`/api/reports/suppliers${query}`);
    const tableBody = document.querySelector("#supplier-rows");
    if (!tableBody) return;
    tableBody.replaceChildren();
    rows.forEach((row) => appendSupplierRow(tableBody, row));
    if (!rows.length) {
      const emptyRow = element("tr");
      const emptyCell = element("td", "empty-row", "No suppliers match this filter.");
      emptyCell.colSpan = 6;
      emptyRow.append(emptyCell);
      tableBody.append(emptyRow);
    }
    const metrics = reportMetrics(rows);
    document.querySelectorAll("#report-metrics strong").forEach((node, index) => {
      node.textContent = metrics[index];
    });
  } catch (reason) {
    toast(reason.message);
  }
}
