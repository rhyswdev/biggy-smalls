"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { api, ApiError } from "@/lib/api";
import type { MessageRow, MessagesView } from "@/lib/types";
import type { AudienceSpec } from "@/lib/writeup/messages";

// The stakeholder messages' mutations (ruling U8). Drafting may call a model once per audience, so it runs only on a
// person's click, never on a page load. The API checks each body again.

export interface MessagesResult { ok: boolean; error?: string; status?: number; view?: MessagesView }
export interface MessageResult { ok: boolean; error?: string; status?: number; row?: MessageRow }

const id = z.string().regex(/^[A-Za-z0-9._-]{1,80}$/);
const audience = z.enum(["exec", "support", "status", "eng"]);
const spec = z.object({
  key: audience, name: z.string().min(1).max(80), channel: z.string().max(120), guide: z.string().max(300),
  checks: z.array(z.string().max(400)).max(12), fallback: z.string().max(4000),
}).strict();
const message = z.string().max(4000);

function failed(error: unknown) {
  if (error instanceof ApiError) return { ok: false, error: error.message, status: error.status };
  return { ok: false, error: "Something went wrong." };
}

/** Draft the audiences given (all four, or one to redraft), replacing their drafts. With `missing`, only those the
 * chat has no draft for yet: the summary's "Draft stakeholder messages" leaves an existing draft alone. */
export async function draftMessages(chatId: string, audiences: AudienceSpec[], missing = false): Promise<MessagesResult> {
  const parsed = z.array(spec).min(1).max(4).safeParse(audiences);
  if (!parsed.success) return { ok: false, error: "The draft request is incomplete." };
  try {
    const chat = id.parse(chatId);
    let wanted = parsed.data;
    if (missing) {
      const have = new Set((await api<MessagesView>(`/chats/${chat}/messages`)).messages.map((m) => m.audience));
      wanted = wanted.filter((a) => !have.has(a.key));
    }
    const view = wanted.length ? await api<MessagesView>(`/chats/${chat}/messages/draft`, { json: { audiences: wanted } })
      : await api<MessagesView>(`/chats/${chat}/messages`);
    revalidatePath(`/chats/${chat}/messages`);
    return { ok: true, view };
  } catch (error) {
    return failed(error);
  }
}

/** The commander's edit. It clears "Copied" unless the text is the exact text copied. */
export async function editMessage(chatId: string, key: string, text: string): Promise<MessageResult> {
  const parsed = z.object({ key: audience, text: message }).safeParse({ key, text });
  if (!parsed.success) return { ok: false, error: "A message is at most 4,000 characters." };
  try {
    const row = await api<MessageRow>(`/chats/${id.parse(chatId)}/messages/${parsed.data.key}`, { method: "PUT", json: { text: parsed.data.text } });
    return { ok: true, row };
  } catch (error) {
    return failed(error);
  }
}

/** Mark this exact text copied, after the browser has put it on the clipboard. */
export async function markCopied(chatId: string, key: string, text: string): Promise<MessageResult> {
  const parsed = z.object({ key: audience, text: message }).safeParse({ key, text });
  if (!parsed.success) return { ok: false, error: "A message is at most 4,000 characters." };
  try {
    const row = await api<MessageRow>(`/chats/${id.parse(chatId)}/messages/${parsed.data.key}/copied`, { json: { text: parsed.data.text } });
    revalidatePath(`/chats/${chatId}/messages`);
    return { ok: true, row };
  } catch (error) {
    return failed(error);
  }
}
