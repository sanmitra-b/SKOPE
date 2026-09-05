import { signOutUser } from "../auth.js";
import { refreshIndex } from "../core/index-status.js";
import { state } from "../core/state.js";
import { renderAbout } from "../pages/about.js";
import { renderAdmin } from "../pages/admin.js";
import { renderChat } from "../pages/chat.js";
import { closeEvidenceDrawer } from "../pages/chat-evidence.js";
import { renderReports } from "../pages/reports.js";
import { renderReviews } from "../pages/reviews.js";


const PAGES = {
  chat: {
    title: "Ask SKOPE",
    subtitle: "Evidence-grounded operational intelligence",
    render: renderChat,
  },
  reports: {
    title: "Supplier reports",
    subtitle: "Performance, exposure, and open commitments",
    render: renderReports,
  },
  admin: {
    title: "System oversight",
    subtitle: "Index health, response quality, and performance",
    render: renderAdmin,
  },
  reviews: {
    title: "Response reviews",
    subtitle: "Quality-control queue and evidence review",
    render: renderReviews,
  },
  about: {
    title: "About SKOPE",
    subtitle: "Architecture, capabilities, and performance",
    render: renderAbout,
  },
};


function userInitials() {
  return (state.user?.displayName || state.user?.email || "SK")
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}


export function renderShell(root) {
  root.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar" id="sidebar">
        <div class="brand"><span class="brand-mark">S</span> SKOPE</div>
        <nav class="nav" aria-label="Primary navigation">
          <button class="nav-button" data-page="chat"><span class="nav-icon"><i class="fa-solid fa-comments"></i></span>Ask SKOPE</button>
          <button class="nav-button" data-page="reports"><span class="nav-icon"><i class="fa-solid fa-file-contract"></i></span>Supplier reports</button>
          <button class="nav-button" data-page="admin"><span class="nav-icon"><i class="fa-solid fa-sliders"></i></span>System oversight</button>
          <button class="nav-button" data-page="reviews"><span class="nav-icon"><i class="fa-solid fa-flag"></i></span>Response reviews</button>
          <button class="nav-button" data-page="about"><span class="nav-icon"><i class="fa-solid fa-circle-info"></i></span>About SKOPE</button>
        </nav>
        <div class="sidebar-footer">
          <div class="account">
            <div class="avatar" id="user-avatar"></div>
            <div class="account-copy"><strong id="user-name"></strong><span id="user-email"></span></div>
            <button class="icon-button" id="logout" aria-label="Sign out"><i class="fa-solid fa-right-from-bracket"></i></button>
          </div>
        </div>
      </aside>
      <section class="workspace">
        <header class="topbar">
          <div class="page-title"><h1 id="page-heading"></h1><span id="page-subtitle"></span></div>
          <div class="status-chip"><span class="status-dot"></span><span id="index-status">Checking corpus…</span></div>
        </header>
        <main class="main-content" id="main-content"></main>
      </section>
    </div>
    <div class="drawer-backdrop" id="drawer-backdrop"></div>
    <aside class="drawer" id="evidence-drawer" aria-label="Evidence details" aria-hidden="true"></aside>`;

  document.querySelector("#user-avatar").textContent = userInitials();
  document.querySelector("#user-name").textContent =
    state.user?.displayName || "SKOPE User";
  document.querySelector("#user-email").textContent = state.user?.email || "";
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.page));
  });
  document.querySelector("#logout").addEventListener("click", signOutUser);
  document.querySelector("#drawer-backdrop").addEventListener(
    "click",
    closeEvidenceDrawer,
  );
  renderCurrentPage();
  refreshIndex();
}


export function navigate(page) {
  const nextPage = PAGES[page] ? page : "chat";
  state.page = nextPage;
  if (location.hash.slice(1) === nextPage) renderCurrentPage();
  else location.hash = nextPage;
}


export function renderCurrentPage() {
  if (!PAGES[state.page]) state.page = "chat";
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === state.page);
  });
  const page = PAGES[state.page];
  document.querySelector("#page-heading").textContent = page.title;
  document.querySelector("#page-subtitle").textContent = page.subtitle;
  page.render();
}
