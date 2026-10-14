"use server";

import { z } from "zod";
import { api, ApiError } from "@/lib/api";
import type { CallView, EvidenceView } from "@/lib/types";

// What a client component asks the API to read: a call's result and a handle's record. The browser never calls
// the API itself, and there is no route handler, so these reads are server actions too.

const id = z.string().regex(/^[A-Za-z0-9._-]{1,80}$/);

export async function readCall(chatId: string, callId: string, runId?: string): Promise<{ call?: CallView; error?: string }> {
  try {
    const q = runId ? `?run_id=${encodeURIComponent(id.parse(runId))}` : "";
    return { call: await api<CallView>(`/chats/${id.parse(chatId)}/calls/${encodeURIComponent(z.string().max(200).parse(callId))}${q}`) };
  } catch (error) {
    return { error: error instanceof ApiError ? error.message : "The result could not be read." };
  }
}

export async function readEvidence(chatId: string, handle: string): Promise<{ evidence?: EvidenceView; error?: string; status?: number }> {
  try {
    const h = z.string().min(1).max(400).parse(handle);
    return { evidence: await api<EvidenceView>(`/chats/${id.parse(chatId)}/evidence/${encodeURIComponent(h)}`) };
  } catch (error) {
    if (error instanceof ApiError) return { error: error.message, status: error.status };
    return { error: "The record could not be read." };
  }
}
