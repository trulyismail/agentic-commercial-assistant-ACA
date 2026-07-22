"use server";

import { revalidatePath } from "next/cache";
import { postSettings } from "@/lib/aca";

export type SettingsState = { error: string | null; saved: boolean };

export async function saveSettings(_prev: SettingsState, formData: FormData): Promise<SettingsState> {
  const values: Record<string, string> = {};
  for (const [key, value] of formData.entries()) {
    if (typeof value === "string") values[key] = value;
  }
  try {
    await postSettings(values);
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e), saved: false };
  }
  revalidatePath("/settings");
  return { error: null, saved: true };
}
