/** Small DOM helpers shared by the page modules. */

export function element(tag, className = "", text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

export function toast(message) {
  const region = document.querySelector("#toast-region");
  if (!region) return;
  const notification = element("div", "toast", message);
  region.append(notification);
  setTimeout(() => notification.remove(), 4_200);
}
