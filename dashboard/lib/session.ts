/**
 * Session minimale à mot de passe unique (§12 item 8) — même contrat que ACA_UI_PASSWORD côté
 * Streamlit : un mot de passe partagé, pas de comptes individuels. Le cookie ne stocke jamais le
 * mot de passe lui-même, seulement un HMAC dérivé (DASHBOARD_SESSION_SECRET) vérifiable côté edge
 * (proxy.ts) sans base de données ni appel réseau.
 *
 * §15.1.7 (expiration de session) : le jeton porte désormais sa propre date d'expiration, SIGNÉE
 * avec lui (`<expiresAt>.<hmac(expiresAt)>`). Auparavant il était constant — un même HMAC restait
 * valable à vie, et le `maxAge` du cookie ne prouvait rien puisqu'il n'est appliqué que par le
 * navigateur : un cookie recopié ailleurs (ou rejoué après sa péremption affichée) passait le
 * contrôle indéfiniment. Ici l'expiration est vérifiée côté serveur, et changer
 * DASHBOARD_SESSION_SECRET invalide immédiatement toutes les sessions en cours — le levier
 * d'invalidation qui manquait.
 */
const COOKIE_NAME = "aca_session";

/** Durée de vie d'une session, en secondes (DASHBOARD_SESSION_TTL_SECONDS, défaut 8 h). */
export function sessionTtlSeconds(): number {
  const configured = Number(process.env.DASHBOARD_SESSION_TTL_SECONDS);
  return Number.isFinite(configured) && configured > 0 ? configured : 8 * 60 * 60;
}

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

function requireSecret(): string {
  const secret = process.env.DASHBOARD_SESSION_SECRET;
  if (!secret) {
    throw new Error("DASHBOARD_SESSION_SECRET n'est pas réglée (.env.local).");
  }
  return secret;
}

/** Émet un jeton valable `sessionTtlSeconds()` secondes à partir de maintenant. */
export async function sessionToken(now: number = Date.now()): Promise<string> {
  const expiresAt = Math.floor(now / 1000) + sessionTtlSeconds();
  return `${expiresAt}.${await hmac(requireSecret(), String(expiresAt))}`;
}

// Comparaison à temps constant de deux chaînes hex (la signature fait toujours 64 caractères hex —
// un SHA-256). Un `===` court-circuite au premier caractère différent : le temps de réponse fuit
// alors la longueur du préfixe correct, permettant de forger le cookie octet par octet. Pur JS
// (pas de dépendance Node `crypto.timingSafeEqual`) pour rester valide sur le runtime edge du
// proxy. La longueur n'est pas secrète (toujours 64), donc le court-circuit de longueur est sûr.
function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

/**
 * Le jeton est-il authentique ET non expiré ? Les deux contrôles sont côté serveur : la signature
 * empêche de fabriquer une date d'expiration lointaine, l'horodatage empêche de rejouer une
 * ancienne session indéfiniment.
 */
export async function isValidSessionToken(
  token: string | undefined,
  now: number = Date.now()
): Promise<boolean> {
  if (!token) return false;
  const separator = token.indexOf(".");
  if (separator <= 0) return false;
  const expiresAt = token.slice(0, separator);
  const signature = token.slice(separator + 1);
  try {
    if (!timingSafeEqualHex(signature, await hmac(requireSecret(), expiresAt))) return false;
  } catch {
    return false;
  }
  const deadline = Number(expiresAt);
  return Number.isFinite(deadline) && Math.floor(now / 1000) < deadline;
}

/**
 * Compare un mot de passe saisi au mot de passe attendu sans fuite de timing. Les deux chaînes sont
 * d'abord réduites à un HMAC de longueur fixe : comparer les clairs directement court-circuiterait
 * au premier caractère différent ET révélerait la longueur du secret. Contrepartie côté dashboard
 * du `hmac.compare_digest` déjà utilisé par `ui.py._check_auth()`.
 */
export async function passwordMatches(candidate: string, expected: string): Promise<boolean> {
  const secret = requireSecret();
  const [a, b] = await Promise.all([hmac(secret, candidate), hmac(secret, expected)]);
  return timingSafeEqualHex(a, b);
}

export { COOKIE_NAME };
