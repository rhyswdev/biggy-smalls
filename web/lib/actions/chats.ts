"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { api, ApiError } from "@/lib/api";
import type { ChatSummary } from "@/lib/types";

// Mutations go through these server actions, which check their input before the API sees it (ruling U7). The
// API checks it again: a second answer to a gate is refused there, whatever happens here.

export interface ActionResult { ok: boolean; error?: string; status?: number }

const id = z.string().regex(/^[A-Za-z0-9._-]{1,80}$/);
const text = (max: number) => z.string().trim().min(1).max(max);

function failed(error: unknown): ActionResult {
  if (error instanceof ApiError) return { ok: false, error: error.message, status: error.status };
  return { ok: false, error: "Something went wrong." };
}

/** Start an investigation in a chat ("Investigate in chat", or a new chat): open the chat it made. */
export async function startChat(input: { origin: "chat" | "blank"; trigger: string; autoApprove: boolean; incident?: string | null }): Promise<ActionResult> {
  const body = z.object({ origin: z.enum(["chat", "blank"]), trigger: text(8000), autoApprove: z.boolean(), incident: z.string().regex(/^INC\d{3,}$/).nullish() }).safeParse(input);
  if (!body.success) return { ok: false, error: "Write a question or a note first." };
  let chat: ChatSummary;
  try {
    const incident = body.data.incident ?? null;
    chat = await api<ChatSummary>("/chats", { json: { origin: incident ? "chat" : body.data.origin, trigger: body.data.trigger, auto_approve: body.data.autoApprove, ...(incident ? { incident } : {}) } });
  } catch (error) {
    return failed(error);
  }
  revalidatePath("/");
  redirect(`/chats/${chat.id}`);
}

/** "Run agent": an unattended run from the commander's note. The dashboard follows it. */
export async function runAgent(): Promise<ActionResult> {
  try {
    await api<ChatSummary>("/chats", { json: { origin: "headless" } });
  } catch (error) {
    return failed(error);
  }
  revalidatePath("/");
  return { ok: true };
}

export async function stopRun(chatId: string): Promise<ActionResult> {
  try {
    await api(`/chats/${id.parse(chatId)}/stop`, { json: {} });
  } catch (error) {
    return failed(error);
  }
  return { ok: true };
}

export async function moreRun(chatId: string, resume = false): Promise<ActionResult> {
  try {
    await api(`/chats/${id.parse(chatId)}/runs`, { json: { resume } });
  } catch (error) {
    return failed(error);
  }
  revalidatePath("/");
  revalidatePath(`/chats/${chatId}`);
  return { ok: true };
}

const Answer = z.object({
  approve: z.boolean().optional(), defer: z.boolean().optional(), reason: z.string().trim().max(2000).optional(),
  args: z.record(z.string(), z.unknown()).optional(),
}).refine((a) => a.approve || a.defer || (a.reason ?? "").length > 0, "a rejection needs a reason");

/** Answer a gate: one answer per call. The API applies it only while the gate is still pending (U5). */
export async function answerGate(chatId: string, interruptId: string, answers: Record<string, z.infer<typeof Answer>>, note?: string): Promise<ActionResult> {
  const parsed = z.object({ chatId: id, interruptId: z.string().min(1).max(200), answers: z.record(z.string().min(1).max(200), Answer), note: z.string().trim().max(4000).optional() })
    .safeParse({ chatId, interruptId, answers, note });
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Invalid answer." };
  try {
    await api(`/chats/${chatId}/gate`, { json: { interrupt_id: interruptId, answers, ...(parsed.data.note ? { note: parsed.data.note } : {}) } });
  } catch (error) {
    return failed(error);
  }
  return { ok: true };
}

export async function sendNote(chatId: string, note: string): Promise<ActionResult & { delivery?: string; handle?: string }> {
  const parsed = z.object({ chatId: id, note: text(4000) }).safeParse({ chatId, note });
  if (!parsed.success) return { ok: false, error: "Write something first." };
  try {
    const r = await api<{ delivery: string; handle: string }>(`/chats/${chatId}/notes`, { json: { text: parsed.data.note } });
    return { ok: true, delivery: r.delivery, handle: r.handle };
  } catch (error) {
    return failed(error);
  }
}

export async function setAutoApprove(chatId: string, on: boolean): Promise<ActionResult> {
  try {
    await api(`/chats/${id.parse(chatId)}/auto-approve`, { method: "PUT", json: { on: z.boolean().parse(on) } });
  } catch (error) {
    return failed(error);
  }
  return { ok: true };
}

const Resolution = z.object({
  path: z.enum(["agent", "own"]), code: z.enum(["Solved (Work Around)", "Solved (Permanently)", "Solved Remotely (Work Around)", "Not Solved (Not Reproducible)"]), note: text(4000), resolvedAt: z.string().trim().regex(/^\d{1,2}:\d{2}$/, "Give the time as HH:MM, in UTC."),
  resolvedBy: z.string().trim().min(1).max(200),
});

/** Resolve the incident in this chat: who found the fix, the resolution code and notes, then on to the review. */
export async function resolveChat(chatId: string, input: z.infer<typeof Resolution>): Promise<ActionResult> {
  const parsed = Resolution.safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Write the resolution notes first." };
  try {
    await api(`/chats/${id.parse(chatId)}/resolution`, { method: "PUT", json: { path: parsed.data.path, code: parsed.data.code, note: parsed.data.note, resolved_at: parsed.data.resolvedAt, resolved_by: parsed.data.resolvedBy } });
  } catch (error) {
    return failed(error);
  }
  revalidatePath(`/chats/${chatId}`);
  revalidatePath("/", "layout");
  redirect(`/chats/${chatId}/resolution`);
}

export interface WriteupResult extends ActionResult { writeup?: { version: number; kept: string[]; saved_at: string | null } | null }

/** Any change to the kept evidence or the note unsaves the write-up until it is saved again. */
export async function editWriteup(chatId: string, kept: string[], note?: string): Promise<WriteupResult> {
  const parsed = z.object({ kept: z.array(z.string().max(400)).max(500), note: z.string().trim().max(4000).optional() }).safeParse({ kept, note });
  if (!parsed.success) return { ok: false, error: "Invalid change." };
  try {
    const r = await api<{ writeup: WriteupResult["writeup"] }>(`/chats/${id.parse(chatId)}/writeup`, { method: "PUT", json: parsed.data });
    return { ok: true, writeup: r.writeup };
  } catch (error) {
    return failed(error);
  }
}

export async function regenerateWriteup(chatId: string): Promise<WriteupResult> {
  try {
    const r = await api<{ writeup: WriteupResult["writeup"] }>(`/chats/${id.parse(chatId)}/writeup/regenerate`, { json: {} });
    return { ok: true, writeup: r.writeup };
  } catch (error) {
    return failed(error);
  }
}

export async function saveWriteup(chatId: string, entry: { version: number; title: string; body: string; terms: string[] }): Promise<WriteupResult> {
  const parsed = z.object({ version: z.number().int().min(1), title: text(300), body: text(40000), terms: z.array(z.string().max(200)).max(100) }).safeParse(entry);
  if (!parsed.success) return { ok: false, error: "The write-up is incomplete." };
  try {
    const r = await api<{ writeup: WriteupResult["writeup"] }>(`/chats/${id.parse(chatId)}/writeup/save`, { json: parsed.data });
    revalidatePath(`/chats/${chatId}/resolution`);
    return { ok: true, writeup: r.writeup };
  } catch (error) {
    return failed(error);
  }
}
