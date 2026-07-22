/**
 * Session minimale à mot de passe unique (§12 item 8) — même contrat que ACA_UI_PASSWORD côté
 * Streamlit : un mot de passe partagé, pas de comptes individuels. Le cookie ne stocke jamais le
 * mot de passe lui-même, seulement un HMAC dérivé (DASHBOARD_SESSION_SECRET) vérifiable côté edge
 * (middleware.ts) sans base de données ni appel réseau.
 */
const COOKIE_NAME = "aca_session";

async function hmac(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return Array.from(new Uint8Array(signature))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function sessionToken(): Promise<string> {
  const secret = process.env.DASHBOARD_SESSION_SECRET;
  if (!secret) {
    throw new Error("DASHBOARD_SESSION_SECRET n'est pas réglée (.env.local).");
  }
  return hmac(secret, "aca-dashboard-session");
}

export async function isValidSessionToken(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  try {
    return token === (await sessionToken());
  } catch {
    return false;
  }
}

export { COOKIE_NAME };
