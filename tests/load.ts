import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ChatEvent } from "@/lib/types";

export function fixture(name: string): ChatEvent[] {
  return JSON.parse(readFileSync(join(__dirname, "fixtures", `${name}.json`), "utf8")) as ChatEvent[];
}

/** A deep copy that throws on any write, to show a fold never changes the state it was given. */
export function frozen<T>(value: T): T {
  const copy = structuredClone(value);
  const freeze = (o: unknown): void => {
    if (o && typeof o === "object") {
      Object.freeze(o);
      for (const v of Object.values(o)) freeze(v);
    }
  };
  freeze(copy);
  return copy;
}
