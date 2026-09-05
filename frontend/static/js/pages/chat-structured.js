import { element } from "../core/dom.js";


const CLIENT_PAGE_SIZE = 25;


function displayLabel(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}


function displayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}


function renderPage(body, rows, columns, page) {
  body.replaceChildren();
  const start = (page - 1) * CLIENT_PAGE_SIZE;
  rows.slice(start, start + CLIENT_PAGE_SIZE).forEach((row) => {
    const tableRow = document.createElement("tr");
    columns.forEach((column) => {
      tableRow.append(element("td", "", displayValue(row[column])));
    });
    body.append(tableRow);
  });
}


function structuredTable(item) {
  const rows = item.rows || [];
  if (!rows.length) return null;
  const metadata = item.metadata || {};
  const columns = (metadata.display_columns || []).filter((column) =>
    Object.prototype.hasOwnProperty.call(rows[0], column),
  );
  if (!columns.length) columns.push(...Object.keys(rows[0]));

  const card = element("section", "structured-result");
  const heading = element("div", "structured-result-head");
  const headingCopy = element("div");
  headingCopy.append(
    element("strong", "", metadata.result_title || item.title || "Structured result"),
    element(
      "span",
      "",
      metadata.truncated
        ? `Showing ${metadata.returned_rows} of ${metadata.total_rows} rows`
        : `${metadata.total_rows ?? rows.length} complete rows`,
    ),
  );
  heading.append(headingCopy);

  const wrap = element("div", "structured-table-wrap");
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach((column) => headRow.append(element("th", "", displayLabel(column))));
  head.append(headRow);
  const body = document.createElement("tbody");
  table.append(head, body);
  wrap.append(table);

  const pageCount = Math.max(1, Math.ceil(rows.length / CLIENT_PAGE_SIZE));
  let page = 1;
  const controls = element("div", "structured-pagination");
  const previous = element("button", "secondary compact", "Previous");
  const status = element("span");
  const next = element("button", "secondary compact", "Next");
  const update = () => {
    renderPage(body, rows, columns, page);
    status.textContent = `Page ${page} of ${pageCount}`;
    previous.disabled = page === 1;
    next.disabled = page === pageCount;
  };
  previous.addEventListener("click", () => {
    page = Math.max(1, page - 1);
    update();
  });
  next.addEventListener("click", () => {
    page = Math.min(pageCount, page + 1);
    update();
  });
  controls.append(previous, status, next);
  update();

  card.append(heading, wrap);
  if (pageCount > 1) card.append(controls);
  return card;
}


export function appendStructuredResults(container, evidence = []) {
  evidence
    .filter((item) => item.evidence_type === "sql" && item.metadata?.render_as_table)
    .forEach((item) => {
      const table = structuredTable(item);
      if (table) container.append(table);
    });
}
