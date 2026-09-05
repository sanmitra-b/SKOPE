import { api } from "../api.js";
import { element, toast } from "../core/dom.js";


const FLAG_EXPLANATIONS = {
  INCOMPLETE_CLAIM_SUPPORT:
    "One or more draft claims could not be verified against their cited evidence and were withheld from the final answer.",
};

let flags = [];
let activeFilter = "OPEN";

const asPercent = (value) => `${(Number(value) * 100).toFixed(0)}%`;

const dateLabel = (value) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? String(value || "—") : parsed.toLocaleString();
};


export async function renderReviews() {
  document.querySelector("#main-content").innerHTML = `
    <div class="page-grid">
      <section class="review-guide">
        <div class="review-guide-icon"><i class="fa-solid fa-flag"></i></div>
        <div>
          <h2>What is an open review flag?</h2>
          <p>SKOPE creates a quality-control flag when a draft contains a claim that its verifier cannot support. Unsupported claims are withheld, so a flag does <strong>not</strong> automatically mean the user received a hallucination.</p>
          <div class="review-steps">
            <span><b>1</b> Read the question and returned answer</span>
            <span><b>2</b> Check the reason and support rate</span>
            <span><b>3</b> Mark reviewed, resolve, or dismiss</span>
          </div>
        </div>
      </section>

      <section class="table-card review-queue-card">
        <div class="table-toolbar">
          <div><h2>Response review queue</h2><span class="subtle">Resolve genuine gaps; dismiss conservative or expected flags</span></div>
          <label class="compact-filter">Show
            <select id="flag-filter">
              <option value="OPEN">Open</option>
              <option value="REVIEWED">Reviewed</option>
              <option value="RESOLVED">Resolved</option>
              <option value="DISMISSED">Dismissed</option>
              <option value="ALL">All</option>
            </select>
          </label>
        </div>
        <div id="flag-list" class="flag-list"><div class="empty-state">Loading review items…</div></div>
      </section>
    </div>`;

  document.querySelector("#flag-filter").value = activeFilter;
  document.querySelector("#flag-filter").addEventListener("change", (event) => {
    activeFilter = event.target.value;
    renderFlags();
  });

  try {
    flags = await api("/api/admin/flags");
    if (document.querySelector("#flag-list")) renderFlags();
  } catch (reason) {
    toast(reason.message);
  }
}


function renderFlags() {
  const list = document.querySelector("#flag-list");
  if (!list) return;
  list.replaceChildren();
  const visible = flags.filter((flag) => activeFilter === "ALL" || flag.status === activeFilter);
  if (!visible.length) {
    const labels = {
      OPEN: "No open review flags.",
      REVIEWED: "No flags awaiting a final decision.",
      RESOLVED: "No resolved review flags.",
      DISMISSED: "No dismissed review flags.",
      ALL: "No review flags have been created.",
    };
    list.append(element("div", "empty-state", labels[activeFilter]));
    return;
  }
  visible.forEach((flag) => list.append(flagCard(flag)));
}


function flagCard(flag) {
  const card = element("article", "flag-card");
  const head = element("div", "flag-card-head");
  const identity = element("div", "flag-identity");
  identity.append(
    element("span", `badge ${String(flag.severity).toLowerCase()}`, flag.severity),
    element("span", `badge status-${String(flag.status).toLowerCase()}`, flag.status),
    element("span", "flag-date", dateLabel(flag.created_at)),
  );
  head.append(
    element("strong", "", String(flag.reason_code).replaceAll("_", " ")),
    identity,
  );

  const explanation = element(
    "p",
    "flag-explanation",
    FLAG_EXPLANATIONS[flag.reason_code] || "This response requires a human quality review.",
  );
  if (flag.details?.supported_claim_rate !== undefined) {
    explanation.append(` Supported claim rate: ${asPercent(flag.details.supported_claim_rate)}.`);
  }

  const withheld = (flag.claims || []).filter((claim) => !claim.supported);
  const withheldPanel = element("div", "withheld-claims");
  withheldPanel.append(element("span", "", "Withheld draft claim"));
  if (withheld.length) {
    const list = element("ul");
    withheld.forEach((claim) => list.append(element("li", "", claim.text)));
    withheldPanel.append(list);
  } else {
    withheldPanel.append(element("p", "", "The rejected claim was not persisted for this older flag."));
  }

  const conversation = element("div", "flag-conversation");
  conversation.append(
    reviewText("Question", flag.question || "Question unavailable"),
    reviewText("Returned answer", flag.answer || "Answer unavailable"),
  );

  const footer = element("div", "flag-card-footer");
  footer.append(
    element(
      "span",
      "subtle",
      flag.reviewed_by
        ? `Last reviewed by ${flag.reviewed_by} · ${dateLabel(flag.reviewed_at)}`
        : `Request ${flag.request_id || "unavailable"}`,
    ),
    actionButtons(flag),
  );

  card.append(head, explanation, withheldPanel);
  const evidence = reviewEvidence(flag.review_evidence || []);
  if (evidence) card.append(evidence);
  card.append(conversation, footer);
  return card;
}


function reviewText(label, text) {
  const container = element("div");
  container.append(element("span", "", label), element("p", "", text));
  return container;
}


function reviewEvidence(evidence) {
  if (!evidence.length) return null;
  const details = element("details", "review-evidence");
  const items = element("div", "review-evidence-list");
  evidence.forEach((item) => {
    const source = element("div", "review-evidence-item");
    source.append(
      element("strong", "", item.title || item.evidence_id),
      element("span", "", [item.evidence_id, item.page ? `page ${item.page}` : null, item.data_as_of].filter(Boolean).join(" · ")),
      element("p", "", item.excerpt || "No compact excerpt is available."),
    );
    items.append(source);
  });
  details.append(element("summary", "", `Inspect cited evidence (${evidence.length})`), items);
  return details;
}


function actionButtons(flag) {
  const actions = element("div", "flag-actions");
  const choices = flag.status === "OPEN"
    ? [["Mark reviewed", "REVIEWED", ""], ["Resolve", "RESOLVED", "positive"], ["Dismiss", "DISMISSED", "muted-action"]]
    : flag.status === "REVIEWED"
      ? [["Resolve", "RESOLVED", "positive"], ["Dismiss", "DISMISSED", "muted-action"], ["Reopen", "OPEN", ""]]
      : [["Reopen", "OPEN", ""]];
  choices.forEach(([label, status, style]) => {
    const button = element("button", `secondary compact ${style}`, label);
    button.addEventListener("click", () => updateFlag(flag.flag_id, status, button));
    actions.append(button);
  });
  return actions;
}


async function updateFlag(flagId, status, button) {
  button.disabled = true;
  try {
    const updated = await api(`/api/admin/flags/${encodeURIComponent(flagId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    flags = flags.map((flag) => flag.flag_id === updated.flag_id ? { ...flag, ...updated } : flag);
    renderFlags();
    toast(`Review flag marked ${status.toLowerCase()}.`);
  } catch (reason) {
    button.disabled = false;
    toast(reason.message);
  }
}
