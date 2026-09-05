import {
  initializeAuth,
  onAuth,
  signOutUser,
  token,
} from "./auth.js";
import { setTokenProvider } from "./api.js";
import { state } from "./core/state.js";
import { renderLogin } from "./views/login.js";
import { renderCurrentPage, renderShell } from "./views/shell.js";


const root = document.querySelector("#app");
setTokenProvider(token);

window.addEventListener("hashchange", () => {
  state.page = location.hash.slice(1) || "chat";
  if (state.user) renderCurrentPage();
});

window.addEventListener("skope-unauthorized", signOutUser);

onAuth((user) => {
  state.user = user;
  if (user) {
    state.page = location.hash.slice(1) || "chat";
    renderShell(root);
  }
  else renderLogin(root);
});

initializeAuth().catch((reason) => {
  renderLogin(root);
  document.querySelector("#login-error").textContent = reason.message;
});
