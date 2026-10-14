import { Handle } from "./Handle";
import { StatusTag } from "./StatusTag";
import { cx, Label } from "./util";

export interface Conflict {
  question: string;
  answers: { source: string; value: React.ReactNode; note?: string; handle?: string }[];
}

/** Sources that disagree, side by side, never merged. */
export function ConflictPair({ conflict, onOpenHandle, className }: { conflict: Conflict; onOpenHandle?: (id: string) => void; className?: string }) {
  return (
    <section className={cx("sm-conflict", className)}>
      <div className="sm-conflict-head">
        <span className="sm-conflict-q">{conflict.question}</span>
        <StatusTag status="awaiting_approval">{conflict.answers.length} sources disagree, not merged</StatusTag>
      </div>
      <div className="sm-conflict-cols">
        {conflict.answers.map((a, i) => (
          <div key={i} className="sm-conflict-col">
            <Label>{a.source}</Label>
            <span className="sm-conflict-val">{a.value}</span>
            {a.note ? <span className="sm-conflict-note">{a.note}</span> : null}
            {a.handle ? <span><Handle id={a.handle} onOpen={onOpenHandle} /></span> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
