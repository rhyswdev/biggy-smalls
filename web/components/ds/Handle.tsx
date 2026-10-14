"use client";

import { cx } from "./util";

export interface HandleProps {
  id: string;
  onOpen?: (id: string) => void;
  active?: boolean;
  missing?: boolean;
  title?: string;
  className?: string;
}

/** A cited evidence handle. "log:host/abc123" renders its kind prefix muted. Pressing it opens the record. */
export function Handle({ id, onOpen, active, missing, title, className }: HandleProps) {
  const i = id.indexOf(":");
  const kind = i > 0 ? id.slice(0, i + 1) : "";
  const tail = i > 0 ? id.slice(i + 1) : id;
  if (missing) {
    return <span className="sm-handle sm-handle-missing" title={`${id} not found in this bundle`}>{kind}{tail}</span>;
  }
  return (
    <button type="button" className={cx("sm-handle", className)} aria-pressed={active ? "true" : undefined} title={title ?? `Open ${id}`}
      data-handle={id} onClick={() => onOpen?.(id)}>
      <span className="sm-handle-kind">{kind}</span>{tail}
    </button>
  );
}

export function Handles({ ids, onOpen, active, none = "None cited" }: { ids: readonly string[] | null | undefined; onOpen?: (id: string) => void; active?: string | null; none?: string }) {
  if (!ids || !ids.length) return <span className="sm-none">{none}</span>;
  return (
    <span className="sm-handles">
      {ids.map((id) => <Handle key={id} id={id} onOpen={onOpen} active={id === active} />)}
    </span>
  );
}
