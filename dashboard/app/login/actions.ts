"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { COOKIE_NAME, passwordMatches, sessionToken, sessionTtlSeconds } from "@/lib/session";

export type LoginState = { error: string | null };

export async function login(_prevState: LoginState, formData: FormData): Promise<LoginState> {
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") ?? "/");
  const expected = process.env.DASHBOARD_PASSWORD;

  if (!expected) {
    return { error: "DASHBOARD_PASSWORD n'est pas configurée côté serveur (.env.local)." };
  }
  // Comparaison à temps constant (§15.1.7) : un `!==` sur le secret court-circuite au premier
  // caractère différent et fuit le préfixe correct par chronométrage.
  if (!(await passwordMatches(password, expected))) {
    return { error: "Mot de passe incorrect." };
  }

  const store = await cookies();
  // `maxAge` est aligné sur l'expiration SIGNÉE dans le jeton lui-même : il ne fait que nettoyer le
  // cookie côté navigateur, c'est `isValidSessionToken` qui refuse réellement une session périmée
  // (le `maxAge` seul était contournable en recopiant le cookie — cf. lib/session.ts).
  store.set(COOKIE_NAME, await sessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: sessionTtlSeconds(),
  });

  redirect(next || "/");
}
