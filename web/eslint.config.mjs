import next from "eslint-config-next";

const config = [
  ...next,
  { ignores: [".next/**", "node_modules/**", "lib/api-types.ts", "test-results/**", "playwright-report/**"] },
];

export default config;
