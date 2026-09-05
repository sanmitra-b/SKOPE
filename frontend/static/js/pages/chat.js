import { api, streamChat } from "../api.js";
import { element, toast } from "../core/dom.js";
import { refreshIndex } from "../core/index-status.js";
import { state } from "../core/state.js";
import {
  appendCitations,
  supportedCitationIds,
} from "./chat-evidence.js";
import { appendStructuredResults } from "./chat-structured.js";


const AGENT_NAMES = [
  "Supplier",
  "Logistics",
  "Inventory",
  "Analytics",
  "Risk",
  "Executive reporting",
];

const SUGGESTIONS = [
  "Which suppliers have late shipments, and what penalties apply?",
  "What were net sales in the last completed quarter?",
  "Summarize open purchase orders by supplier risk tier.",
  "Which warehouse shortages need immediate action?",
];


function chatTemplate() {
  const suggestions = SUGGESTIONS.map(
    (suggestion) => `<button class="suggestion">${suggestion}</button>`,
  ).join("");
  const agents = AGENT_NAMES.map(
    (name) => `
      <div class="agent-row">
        <span>${name}</span><span class="agent-state">Standby</span>
      </div>`,
  ).join("");

  return `
    <div class="chat-layout">
      <section class="chat-panel">
        <div class="chat-empty" id="chat-empty">
          <div class="chat-empty-inner">
            <div class="eyebrow">Multi-source analysis</div>
            <h2>What needs your attention today?</h2>
            <p>Ask across operational records and enterprise documents. SKOPE shows the routed agents and evidence behind every supported claim.</p>
            <div class="suggestions">${suggestions}</div>
          </div>
        </div>
        <div class="messages" id="messages" aria-live="polite"></div>
        <form class="composer" id="chat-form">
          <div class="composer-box">
            <textarea id="query" rows="1" maxlength="4000"
              placeholder="Ask about suppliers, shipments, inventory, contracts, or risk…"
              aria-label="Ask SKOPE"></textarea>
            <button class="send-button" id="send" type="submit" aria-label="Send question">
              <i class="fa-solid fa-arrow-up"></i>
            </button>
          </div>
          <div class="composer-meta">
            <span>Enter to send · Shift+Enter for a new line</span>
            <span>Answers may be partial when evidence is insufficient</span>
          </div>
        </form>
      </section>
      <aside class="side-panel">
        <section class="panel-card">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
            <h3 style="margin:0">Conversations</h3>
            <button class="icon-button" id="new-chat" aria-label="Start a new conversation">
              <i class="fa-solid fa-plus"></i>
            </button>
          </div>
          <div class="conversation-list" id="conversation-list"><span class="subtle">Loading…</span></div>
        </section>
        <section class="panel-card">
          <h3>Agent activity</h3>
          <div class="agent-list" id="agent-list">${agents}</div>
        </section>
        <section class="panel-card">
          <h3>Evidence boundary</h3>
          <div class="metric-mini">
            <div><strong id="corpus-docs">—</strong><span>source documents</span></div>
            <div><strong id="corpus-records">—</strong><span>source records</span></div>
          </div>
          <p class="subtle" style="font-size:11px;margin-bottom:0">
            Dense + sparse retrieval, exact-ID resolution, local reranking, and guarded read-only SQL.
          </p>
        </section>
      </aside>
    </div>`;
}


export function renderChat() {
  document.querySelector("#main-content").innerHTML = chatTemplate();
  document.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => {
      const query = document.querySelector("#query");
      query.value = button.textContent;
      query.focus();
    });
  });
  document.querySelector("#query").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      document.querySelector("#chat-form").requestSubmit();
    }
  });
  document.querySelector("#chat-form").addEventListener("submit", sendQuestion);
  document.querySelector("#new-chat").addEventListener("click", startNewConversation);
  loadConversations();
  refreshIndex();
}


function startNewConversation() {
  state.conversationId = null;
  state.evidence = new Map();
  const messages = document.querySelector("#messages");
  messages.replaceChildren();
  messages.classList.remove("visible");
  document.querySelector("#chat-empty").style.display = "grid";
  loadConversations();
}


async function loadConversations() {
  const list = document.querySelector("#conversation-list");
  if (!list) return;
  try {
    const conversations = await api("/api/conversations");
    list.replaceChildren();
    if (!conversations.length) {
      list.append(element("span", "subtle", "No saved conversations yet."));
      return;
    }
    conversations.slice(0, 8).forEach((conversation) => {
      const isActive = conversation.conversation_id === state.conversationId;
      const button = element(
        "button",
        `conversation-item ${isActive ? "active" : ""}`,
      );
      button.type = "button";
      button.append(
        element("strong", "", conversation.title || "Untitled conversation"),
        element("span", "", new Date(conversation.updated_at).toLocaleDateString()),
      );
      button.addEventListener(
        "click",
        () => restoreConversation(conversation.conversation_id),
      );
      list.append(button);
    });
  } catch {
    list.replaceChildren(
      element("span", "subtle", "Conversation history unavailable."),
    );
  }
}


async function restoreConversation(conversationId) {
  try {
    const records = await api(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
    );
    state.conversationId = conversationId;
    state.evidence = new Map();
    const messages = document.querySelector("#messages");
    messages.replaceChildren();

    for (const record of records) {
      const role = record.role === "user" ? "user" : "assistant";
      const message = appendMessage(role, record.content);
      if (role === "user") continue;

      for (const item of record.evidence || []) {
        state.evidence.set(item.evidence_id, item);
      }
      appendStructuredResults(message, record.evidence || []);
      appendCitations(message, supportedCitationIds(record.claims || []));
      for (const warning of record.warnings || []) {
        message.append(element("div", "warning", warning));
      }
      if (record.routing?.agents) setAgentStates(record.routing.agents);
    }
    messages.scrollTop = messages.scrollHeight;
    loadConversations();
  } catch (reason) {
    toast(reason.message);
  }
}


function appendMessage(role, text) {
  document.querySelector("#chat-empty").style.display = "none";
  const messages = document.querySelector("#messages");
  messages.classList.add("visible");
  const message = element("article", `message ${role}`);
  if (role === "assistant") {
    message.append(element("div", "message-head", "SKOPE · grounded response"));
  }
  message.append(element("div", "answer", text));
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
  return message;
}


function setAgentStates(routing = [], results = []) {
  const resultMap = new Map(
    results.map((result) => [result.agent.replaceAll("_", " "), result.status]),
  );
  document.querySelectorAll("#agent-list .agent-row").forEach((row) => {
    const displayName = row.firstElementChild.textContent.toLowerCase();
    const canonicalName =
      displayName === "executive reporting" ? "executive_reporting" : displayName;
    const status =
      resultMap.get(displayName) ||
      (routing.includes(canonicalName) ? "routed" : "standby");
    const statusNode = row.lastElementChild;
    statusNode.textContent = status[0].toUpperCase() + status.slice(1);
    statusNode.className = `agent-state ${status}`;
  });
}


async function sendQuestion(event) {
  event.preventDefault();
  if (state.busy) return;
  const input = document.querySelector("#query");
  const question = input.value.trim();
  if (!question) return;

  state.busy = true;
  input.value = "";
  document.querySelector("#send").disabled = true;
  appendMessage("user", question);
  const waiting = appendMessage(
    "assistant",
    "Routing the question and collecting evidence…",
  );
  waiting.prepend(element("div", "loading-line"));

  try {
    await streamChat(
      { query: question, conversation_id: state.conversationId },
      (eventName, data) => handleChatEvent(eventName, data, waiting),
    );
  } catch (reason) {
    waiting.replaceChildren(
      element("div", "message-head", "SKOPE · request interrupted"),
      element("div", "answer", reason.message),
    );
    toast(reason.message);
  } finally {
    state.busy = false;
    document.querySelector("#send").disabled = false;
    input.focus();
  }
}


function handleChatEvent(eventName, data, waiting) {
  if (eventName === "status") {
    waiting.querySelector(".answer").textContent = data.message;
    return;
  }
  if (eventName === "error") throw new Error(data.message);
  if (eventName !== "result") return;

  state.conversationId = data.conversation_id;
  // Keep evidence from earlier answers in the active conversation. Replacing
  // the map here left previous citation buttons visible but unable to resolve
  // their evidence after the user asked a follow-up question.
  for (const item of data.evidence) {
    state.evidence.set(item.evidence_id, item);
  }
  waiting.replaceChildren(
    element("div", "message-head", "SKOPE · grounded response"),
    element("div", "answer", data.answer),
  );
  appendStructuredResults(waiting, data.evidence);
  appendCitations(waiting, supportedCitationIds(data.claims));
  for (const warning of data.warnings || []) {
    waiting.append(element("div", "warning", warning));
  }
  setAgentStates(data.routing.agents, data.agents);
  loadConversations();
}
