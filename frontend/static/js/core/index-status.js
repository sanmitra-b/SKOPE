import { api } from "../api.js";
import { state } from "./state.js";

function setStatus(text, color) {
  const label = document.querySelector("#index-status");
  const indicator = document.querySelector(".status-dot");
  if (label) label.textContent = text;
  if (indicator) indicator.style.background = color;
}

export async function refreshIndex() {
  try {
    state.index = await api("/api/index/status");
    const ready =
      state.index.qdrant_points > 0 &&
      state.index.qdrant_points === state.index.postgres_chunks;

    setStatus(
      ready ? "Database online" : "Database indexing",
      ready ? "var(--mint)" : "var(--amber)",
    );
    document.querySelector("#corpus-docs")?.replaceChildren(
      document.createTextNode(state.index.production_documents.toLocaleString()),
    );
    document.querySelector("#corpus-records")?.replaceChildren(
      document.createTextNode(state.index.total_source_records.toLocaleString()),
    );
  } catch {
    setStatus("Backend unavailable", "var(--red)");
  }
}
