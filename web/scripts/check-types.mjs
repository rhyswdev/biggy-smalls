// lib/api-types.ts is generated from lib/openapi.json and never edited by hand: regenerate it and compare.
// (A Python test compares lib/openapi.json with the API itself, so the chain reaches the running schema.)
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const fresh = execFileSync(join(root, "node_modules", ".bin", "openapi-typescript"), [join(root, "lib", "openapi.json")], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
const committed = readFileSync(join(root, "lib", "api-types.ts"), "utf8");
if (fresh.trim() !== committed.trim()) {
  console.error("lib/api-types.ts differs from what lib/openapi.json generates: run `make api-types`");
  process.exit(1);
}
console.log("api types: lib/api-types.ts matches lib/openapi.json");
