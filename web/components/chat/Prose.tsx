"use client";

import { Fragment } from "react";
import { blocks, runs, type Line, type Run } from "@/lib/chat/prose";
import { Linked } from "./Linked";

type Open = ((h: string) => void) | undefined;

/** The model's prose with the markdown it writes shown as formatting (Q7): paragraphs, lists and headings, with
 * bold and code inside them. It is read by `blocks` into plain data and built from strings and elements, never
 * parsed as HTML. Handles are buttons, as in Linked, when there is a drawer to open them in. */
export function Prose({ text, onOpen, className = "narr", style }: { text: string; onOpen?: (h: string) => void; className?: string; style?: React.CSSProperties }) {
  return (
    <>
      {blocks(text).map((b, i) => {
        if (b.kind === "ul" || b.kind === "ol") {
          const items = b.items.map((item, j) => <li key={j}><Lines lines={item} onOpen={onOpen} /></li>);
          return b.kind === "ul"
            ? <ul key={i} className={className} style={style}>{items}</ul>
            : <ol key={i} className={className} style={style} start={b.start}>{items}</ol>;
        }
        const body = <Lines lines={b.lines} onOpen={onOpen} />;
        return <p key={i} className={className} style={style}>{b.kind === "h" ? <strong>{body}</strong> : body}</p>;
      })}
    </>
  );
}

/** One line of prose, such as a lead, with its bold and code and no blocks. */
export function Inline({ text, onOpen }: { text: string; onOpen?: (h: string) => void }) {
  return <Runs runs={runs(text)} onOpen={onOpen} />;
}

function Lines({ lines, onOpen }: { lines: Line[]; onOpen: Open }) {
  return <>{lines.map((l, i) => <Fragment key={i}>{i ? <br /> : null}<Runs runs={l} onOpen={onOpen} /></Fragment>)}</>;
}

function Runs({ runs, onOpen }: { runs: Run[]; onOpen: Open }) {
  return (
    <>
      {runs.map((r, i) => {
        const text = onOpen ? <Linked text={r.text} onOpen={onOpen} /> : r.text;
        const code = r.code ? <code>{text}</code> : text;
        return <Fragment key={i}>{r.bold ? <strong>{code}</strong> : code}</Fragment>;
      })}
    </>
  );
}
