"use client";

import { useEffect, useRef, useState } from "react";
import { apply, type ChatState } from "@/lib/chat/reduce";
import type { ChatEvent } from "@/lib/types";

// Every event type of the agent's protocol (smalls/agent/events.py). The stream names each event, and an
// EventSource only hands a named event to a listener for that name.
export const EVENT_TYPES = [
  "run_started", "phase", "heartbeat", "thinking_delta", "thinking", "text_delta", "model_call", "tool_call_proposed", "gate_pending",
  "gate_decision", "run_paused", "tool_started", "tool_result", "hypothesis_update", "board_status", "stuck", "restart", "ic_note",
  "auto_approve", "budget", "draft_report", "verifier_verdict", "validation", "report", "cache_ratio_low", "error", "answer",
  "followup_finished", "run_finished",
] as const;

/** The chat's state, folded from the first paint's state and then from each event the stream brings. The
 * stream is reached through the web app's own rewrite, so the browser never learns the API's address; a
 * dropped connection reconnects and resumes from the last event it saw. */
export function useChatEvents(chatId: string, initial: ChatState, live = true): ChatState {
  const [state, setState] = useState(initial);
  const first = useRef(initial.lastSeq);
  useEffect(() => {
    if (!live) return;
    const source = new EventSource(`/events/${encodeURIComponent(chatId)}?after=${first.current}`);
    const onEvent = (e: MessageEvent<string>) => {
      try {
        const ev = JSON.parse(e.data) as ChatEvent;
        setState((s) => apply(s, ev));
      } catch {
        // a torn message is skipped; the next one carries on
      }
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, onEvent);
    return () => source.close();
  }, [chatId, live]);
  return state;
}
