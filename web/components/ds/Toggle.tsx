"use client";

import { cx } from "./util";

export interface ToggleProps {
  checked: boolean;
  onChange?: (on: boolean) => void;
  label: React.ReactNode;
  hint?: React.ReactNode;
  disabled?: boolean;
  className?: string;
}

/** A switch: a button with role="switch" and aria-checked. */
export function Toggle({ checked, onChange, label, hint, disabled, className }: ToggleProps) {
  return (
    <button type="button" role="switch" aria-checked={checked ? "true" : "false"} className={cx("sm-toggle", className)} disabled={disabled}
      onClick={() => onChange?.(!checked)}>
      <span className="sm-toggle-track" aria-hidden="true" />
      <span>{label}{hint ? <span className="sm-toggle-hint">{hint}</span> : null}</span>
    </button>
  );
}
