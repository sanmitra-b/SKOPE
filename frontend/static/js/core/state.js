/** Mutable UI session state. Server data remains authoritative. */

export const state = {
  user: null,
  page: location.hash.slice(1) || "chat",
  conversationId: null,
  evidence: new Map(),
  index: null,
  busy: false,
};
