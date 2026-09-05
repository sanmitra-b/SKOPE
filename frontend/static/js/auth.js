const CONFIG = window.SKOPE_CONFIG || {};
const AUTH_MODE = CONFIG.authMode || "dev";
const FIREBASE_VERSION = "12.2.1";
const listeners = new Set();
let current = null;
let firebaseAuth = null;
let firebaseAuthModule = null;

function emit() {
  listeners.forEach((listener) => listener(current));
}

export function authMode() {
  return AUTH_MODE;
}

export function onAuth(listener) {
  listeners.add(listener);
  listener(current);
  return () => listeners.delete(listener);
}

export async function initializeAuth() {
  if (AUTH_MODE === "dev") {
    const saved = sessionStorage.getItem("skope.dev.session");
    if (saved) current = JSON.parse(saved);
    emit();
    return;
  }

  const appModuleUrl = `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app.js`;
  const authModuleUrl = `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-auth.js`;
  const [{ initializeApp }, authModule] = await Promise.all([
    import(appModuleUrl),
    import(authModuleUrl),
  ]);
  firebaseAuthModule = authModule;
  const firebaseConfig = CONFIG.firebase || {};
  if (!firebaseConfig.apiKey || !firebaseConfig.projectId || !firebaseConfig.appId) {
    throw new Error("Firebase client configuration is incomplete.");
  }
  firebaseAuth = authModule.getAuth(initializeApp(firebaseConfig));
  authModule.onAuthStateChanged(firebaseAuth, (user) => {
    current = user;
    emit();
  });
}

export async function signIn(email, password) {
  if (AUTH_MODE === "dev") {
    const devToken = password;
    if (!devToken) {
      throw new Error("Enter the local API token.");
    }
    current = {
      uid: "local-admin",
      email: email || "admin@skope.local",
      displayName: "Local Administrator",
      token: devToken,
    };
    sessionStorage.setItem("skope.dev.session", JSON.stringify(current));
    emit();
    return current;
  }
  return firebaseAuthModule.signInWithEmailAndPassword(firebaseAuth, email, password);
}

export async function signInWithGoogle() {
  if (AUTH_MODE === "dev") return signIn("admin@skope.local", "dev");
  const provider = new firebaseAuthModule.GoogleAuthProvider();
  return firebaseAuthModule.signInWithPopup(firebaseAuth, provider);
}

export async function signOutUser() {
  if (AUTH_MODE === "dev") {
    sessionStorage.removeItem("skope.dev.session");
    current = null;
    emit();
    return;
  }
  await firebaseAuthModule.signOut(firebaseAuth);
}

export async function token() {
  if (!current) return null;
  if (AUTH_MODE === "dev") return current.token;
  return current.getIdToken();
}
