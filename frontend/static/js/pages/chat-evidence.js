import { openDocumentFile } from "../api.js";
import { element, toast } from "../core/dom.js";
import { state } from "../core/state.js";


export function supportedCitationIds(claims) {
  return [
    ...new Set(
      claims
        .filter((claim) => claim.supported)
        .flatMap((claim) => claim.evidence_ids || []),
    ),
  ];
}


export function appendCitations(container, citationIds) {
  if (!citationIds.length) return;
  const citations = element("div", "citations");
  citationIds.forEach((id, index) => {
    const title = state.evidence.get(id)?.title || id;
    const button = element("button", "citation-link", `${index + 1} · ${title}`);
    button.addEventListener("click", () => openEvidence(id));
    citations.append(button);
  });
  container.append(citations);
}


export function openEvidence(evidenceId) {
  const item = state.evidence.get(evidenceId);
  if (!item) return;

  const drawer = document.querySelector("#evidence-drawer");
  drawer.replaceChildren();
  drawer.append(_evidenceHeader(item), _evidenceMetadata(item));

  if (item.excerpt) drawer.append(element("div", "excerpt", item.excerpt));
  if (item.sql) _appendSqlEvidence(drawer, item);
  if (item.document_id) _appendSourceButton(drawer, item.document_id);

  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.querySelector("#drawer-backdrop").classList.add("open");
}


function _evidenceHeader(item) {
  const heading = element("div");
  heading.append(
    element(
      "div",
      "eyebrow",
      item.evidence_type === "sql" ? "Structured evidence" : "Document evidence",
    ),
    element("h2", "", item.title),
  );

  const closeButton = element("button", "icon-button");
  closeButton.innerHTML = '<i class="fa-solid fa-xmark"></i>';
  closeButton.setAttribute("aria-label", "Close evidence");
  closeButton.addEventListener("click", closeEvidenceDrawer);

  const header = element("div", "drawer-head");
  header.append(heading, closeButton);
  return header;
}


function _evidenceMetadata(item) {
  const metadata = element("div", "evidence-meta");
  const values = [
    item.page ? `Page ${item.page}` : null,
    item.section,
    item.data_as_of ? `As of ${item.data_as_of}` : null,
    item.score !== null && item.score !== undefined ? `Score ${item.score}` : null,
  ];
  values
    .filter(Boolean)
    .forEach((value) => metadata.append(element("span", "", value)));
  return metadata;
}


function _appendSqlEvidence(drawer, item) {
  drawer.append(
    element("h3", "", "Validated SQL"),
    element("div", "excerpt", item.sql),
    element("h3", "", "Result rows"),
    element("div", "excerpt", JSON.stringify(item.rows, null, 2)),
  );
}


function _appendSourceButton(drawer, documentId) {
  const sourceButton = element("button", "secondary", "Open source file");
  sourceButton.style.cssText = "display:block;text-align:center;margin-top:18px";
  sourceButton.addEventListener("click", () => {
    openDocumentFile(documentId).catch((reason) => toast(reason.message));
  });
  drawer.append(sourceButton);
}


export function closeEvidenceDrawer() {
  document.querySelector("#evidence-drawer")?.classList.remove("open");
  document.querySelector("#evidence-drawer")?.setAttribute("aria-hidden", "true");
  document.querySelector("#drawer-backdrop")?.classList.remove("open");
}
