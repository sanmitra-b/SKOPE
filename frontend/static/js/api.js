const API_BASE = window.SKOPE_CONFIG?.apiBaseUrl || "";

let tokenProvider = async () => null;

export function setTokenProvider(provider) {
  tokenProvider = provider;
}

async function headers(extra = {}) {
  const token = await tokenProvider();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

export async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: await headers(options.headers),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("skope-unauthorized"));
    }
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

export async function streamChat(payload, onEvent) {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    throw new Error(`Chat request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const packets = buffer.split("\n\n");
    buffer = packets.pop() || "";
    packets.forEach((packet) => dispatchServerEvent(packet, onEvent));
  }
}

function dispatchServerEvent(packet, onEvent) {
  let eventName = "message";
  let data = "";
  for (const line of packet.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (data) onEvent(eventName, JSON.parse(data));
}

export async function download(path, filename) {
  const response = await fetch(`${API_BASE}${path}`, { headers: await headers() });
  if (!response.ok) {
    throw new Error(`Download request failed (${response.status})`);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export async function openDocumentFile(documentId) {
  const response = await fetch(`${API_BASE}/api/documents/${encodeURIComponent(documentId)}/file`, {
    headers: await headers(),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Source file request failed (${response.status})`);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  window.open(objectUrl, "_blank", "noopener,noreferrer");
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}
