import { authMode, signIn, signInWithGoogle } from "../auth.js";


export function renderLogin(root) {
  const isDevelopment = authMode() === "dev";
  root.innerHTML = `
    <main class="login-page">
      <section class="login-visual" aria-label="SKOPE introduction">
        <div class="brand"><span class="brand-mark">S</span> SKOPE</div>
        <div class="hero-copy">
          <div class="eyebrow">Supply chain knowledge · operational clarity</div>
          <h1>Supply Chain Knowledge and Operations AI engine</h1>
          <p>Ask one question across contracts, purchase orders, shipments, inventory, warehouse reports, and enterprise communications.</p>
          <div class="capability-strip" aria-label="Capabilities">
            <span>Hybrid retrieval</span>
            <span>Guarded ERP analytics</span>
            <span>Six specialist agents</span>
            <span>Claim-level citations</span>
          </div>
        </div>
      </section>
      <section class="login-panel">
        <form class="login-card" id="login-form">
          <div class="brand"><span class="brand-mark">S</span> SKOPE</div>
          <h2>Welcome back</h2>
          <p class="subtle">Sign in to your supply chain intelligence workspace.</p>
          <div class="field">
            <label for="email">Work email</label>
            <input id="email" type="email" autocomplete="email" value="admin@skope.local" required>
          </div>
          <div class="field">
            <label for="password">${isDevelopment ? "Local API token" : "Password"}</label>
            <input id="password" type="password" autocomplete="current-password"
              placeholder="${isDevelopment ? "Same value as backend DEV_AUTH_TOKEN" : "Your password"}" required>
          </div>
          <p id="login-error" class="form-error" role="alert"></p>
          <button class="primary" type="submit">Sign in securely</button>
          ${isDevelopment ? "" : `
            <div class="divider">or</div>
            <button class="secondary" id="google-login" type="button">Continue with Google SSO</button>
          `}
        </form>
      </section>
    </main>`;

  const error = document.querySelector("#login-error");
  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      await signIn(
        document.querySelector("#email").value,
        document.querySelector("#password").value,
      );
    } catch (reason) {
      error.textContent = reason.message;
    }
  });
  const googleLogin = document.querySelector("#google-login");
  if (googleLogin) {
    googleLogin.addEventListener("click", async () => {
      try {
        await signInWithGoogle();
      } catch (reason) {
        error.textContent = reason.message;
      }
    });
  }
}
