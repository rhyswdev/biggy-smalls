"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { api, ApiError } from "@/lib/api";
import type { ActionResult } from "./chats";

/** Import a ServiceNow incident by number or link. Smalls starts its diagnosis straight away, and the browser
 * lands on its overview. An unknown number comes back as the API's sentence, for the form to show. */
export async function importIncident(number: string): Promise<ActionResult> {
  const parsed = z.string().trim().min(1).max(300).safeParse(number);
  if (!parsed.success) return { ok: false, error: "Enter an incident number first." };
  try {
    await api<{ chat_id: string }>("/incidents/import", { json: { number: parsed.data } });
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, error: error.message, status: error.status };
    return { ok: false, error: "Something went wrong." };
  }
  revalidatePath("/", "layout");
  redirect("/");
}

