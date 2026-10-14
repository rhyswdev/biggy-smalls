import { cx } from "./util";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm";
}

/** A real button: secondary unless told otherwise, 32 px high, 24 px when small. */
export function Button({ variant = "secondary", size, className, type = "button", ...rest }: ButtonProps) {
  return <button type={type} {...rest} className={cx("sm-btn", `sm-btn-${variant}`, size === "sm" && "sm-btn-sm", className)} />;
}
