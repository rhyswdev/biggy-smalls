"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "./Button";
import { cx } from "./util";

/** A drafted message with a Copy button that says when it copied. */
export function StakeholderMessage({ audience, channel, text, asOf, nextUpdate, onCopy, className }: {
  audience: string; channel?: string; text: string; asOf?: string; nextUpdate?: string; onCopy?: (text: string) => void; className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  function copy() {
    const done = () => { setCopied(true); timer.current = setTimeout(() => setCopied(false), 1600); };
    onCopy?.(text);
    navigator.clipboard?.writeText(text).then(done, done) ?? done();
  }
  return (
    <article className={cx("sm-msg", className)}>
      <div className="sm-msg-head">
        <span className="sm-msg-aud">{audience}</span>
        {channel ? <span className="sm-muted" style={{ fontSize: 12 }}>{channel}</span> : null}
        <Button size="sm" onClick={copy} aria-live="polite">{copied ? "Copied" : "Copy"}</Button>
      </div>
      <div className="sm-msg-body">{text}</div>
      {asOf || nextUpdate ? (
        <div className="sm-msg-foot">
          {asOf ? <span>As of <span className="sm-mono">{asOf} UTC</span></span> : null}
          {nextUpdate ? <span>Next update <span className="sm-mono">{nextUpdate} UTC</span></span> : null}
        </div>
      ) : null}
    </article>
  );
}
